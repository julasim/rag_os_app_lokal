# Architektur — RAG OS (native App)

Entwicklersicht auf das System, wie es **heute** gebaut ist (nach dem Docker-freien
Umbau M1–M8). Für den empfohlenen Lese-Einstieg in den Code siehe **[../CLAUDE.md](../CLAUDE.md)
§2 „Goldener Pfad"**; dieses Dokument gibt den Überblick drumherum.

> Stand: 2026-07-21 · nativ, kein Docker/Qdrant/Postgres/Ollama/OAuth.

---

## 1. Kernprinzip

**Ingest (schwer, selten, EIN Schreiber) und Abfrage (leicht, oft, viele Leser) sind
getrennt.** Sicherheit bei geteiltem Speicher entsteht durch **unveränderliche,
versionierte Index-Stände** (Git-Prinzip: LanceDB-Tags `current`/`prev`), nicht durch
einen Live-Server. Alles läuft in **einem `uvicorn`-Prozess** auf `127.0.0.1` in einer
WebView2-Shell — kein Netzwerk-Dienst, keine offenen Ports nach außen.

## 2. Speicher — zwei Orte, klare Rollen

| Speicher | Wo | Inhalt | Zugriff |
|---|---|---|---|
| **LanceDB** (`chunks`) | im **Vault** (`.ragos/index.lance`) | Chunk-Vektoren + Payload (Ordner/Tags/Metadaten), FTS-Index — „**wo** steht es" | `pipelines/store.py` |
| **state.sqlite** | im **Vault** (`.ragos/state.sqlite`) | Dokumente, Chunk-Meta, **Graph**, Query-Log, Jobs — „**was** haben wir" (pro Firma) | `get_session()` |
| **credentials.sqlite** | **lokal** (`%LOCALAPPDATA%\RAG-OS`) | `ui_users` + `api_keys` — nie auf NAS, maschinenweit über alle Firmen | `get_local_session()` |

**Multi-Vault (Firmen-Trennung):** Content liegt **im** Vault → eine Firma = ein portabler
Ordner. Credentials bleiben **lokal**. Vault wechseln: Tray „Vault (Firma)" → Neustart.
Keine DB-übergreifenden FKs; Vault-DB nutzt Rollback-Journal (SMB-tauglich). Details:
[../CLAUDE.md](../CLAUDE.md) §4.

Driften die beiden auseinander (Doc in SQLite, kein Chunk in LanceDB) → **Bug**, nicht
Feature. Es gibt **genau eine** LanceDB-Tabelle für alle Dokumente (keine Collection pro
irgendetwas) und **kein „Projekt"-Konzept** — nur `folder_path` (frei, nestbar) + Tags.

## 3. Datenfluss Ingest (Schreiber)

```
Datei → SHA256 (Dedup pro Ordner) → Parser → Chunking → Embedding → Metadaten → LanceDB
```

- **Parser (`ingest_backend`):** `docling` (layout-aware, Tabellen verlustfrei, gebündelte
  Modelle) oder `legacy` (PyMuPDF/python-docx, schnell). Gebündelt, offline, kein
  Runtime-Download.
- **Embedding:** INT8-ONNX `multilingual-e5-large` (1024-dim) über onnxruntime
  ([pipelines/factory.py](../app/pipelines/factory.py), Mean-Pooling + e5-Präfixe). Der
  Embedding-Schritt **dominiert** die Ingest-Zeit, nicht der Parser (M8g).
- **Metadaten + Tags: deterministisch, kein LLM** ([ingest/metadata_extract.py](../app/ingest/metadata_extract.py),
  [ingest/autotag.py](../app/ingest/autotag.py)) — `doc_type`/`norm_id`/`version`/`issuer`
  + Termfrequenz-Tags über dem ganzen Text.
- Chunks werden kanonisch nach SQLite (`DocumentChunk`) geschrieben, dann nach LanceDB.
- Blockierende Modell-/Store-Calls laufen in `asyncio.to_thread` (sonst blockiert der Eventloop).

## 4. Datenfluss Abfrage (retrieve-only, MCP)

`rag_retrieve` ([mcp_server/](../app/mcp_server/)) → `run_retrieve`
([pipelines/query.py](../app/pipelines/query.py)):

1. **ACL serverseitig auflösen** (nie dem Client-`folder` vertrauen): Bearer-Key
   (`accessible_folder_paths`, leer = alles) vs. User (`user_accessible_folder_paths`,
   fail-safe: leer = nichts) → erlaubte Ordner.
2. **Hybrid-Retrieval:** dichtes INT8-e5-large + LanceDB-FTS (BM25) via RRF — dense für
   Bedeutung, lexikalisch für exakte §/Normnummern/Codes.
3. **Graph-Augmentierung** (ACL-restringiert, §13): Norm-Referenz-Fastpath + PPR über den
   sichtbaren Subgraph.
4. **Reranker** (bge-reranker-v2-m3 INT8-ONNX, default an) → Top-k.
5. **Sanitize + Status:** ACL-Nachfilter (`_sanitize_chunks`), `outdated`/`superseded_by`
   frisch aus SQLite. Rückgabe = **nur Chunks + Quellen + Zitat**, keine LLM-Antwort.

## 5. Auth & Ordner-ACL

**Eine** Quelle: [auth/folders.py](../app/auth/folders.py) (`is_within` / `key_allows_folder`
/ `user_allows_folder` / `accessible_folder_paths`) — segmentgrenzbewusst, **nie** nacktes
`startswith`. Zwei getrennte Semantiken: Bearer-Key (leer = alles) vs. UI/User (fail-safe,
leer = nichts). Neue Endpunkte hängen sich hier ein, nicht mit eigener Logik. MCP ist
Bearer-only, read-only; UI hat lokalen Auto-Login (127.0.0.1).

## 6. Wissensgraph

Deterministisch (kein LLM): L1 (Regex-Normverweise + supersedes/issued_by/has_tag/
in_folder), L2 (Ähnlichkeit: similar_to / near_dup), Analyse (Louvain-Communities +
PageRank). Working-Store = `graph_*`-Tabellen in der Vault-`state.sqlite`; Retrieval liest einen
RAM-Snapshot ([graph/store.py](../app/graph/store.py)). **Visualisierung** liest eine flache
`.ragos/graph.json` im Vault (vom manuellen Rebuild geschrieben, Writer-only); `GET
/api/graph` ist **pro Aufrufer ACL-gefiltert** — Schnittmenge, nie Vereinigung (§13).

## 7. Rollen & Versionierung

- **Schreiber:** Ingest + Query, baut neue LanceDB-Versionen im Vault. `publish()`
  ([pipelines/publish.py](../app/pipelines/publish.py)) taggt die neueste Version `current`,
  rollt die alte nach `prev` (Retention K=2).
- **Leser:** query-only. `sync_reader_cache()` kopiert das getaggte Vault-Dataset in einen
  lokalen Cache (SMB nur Transport, nie Live-Query), `checkout_current()` pinnt `current`
  für die Query-Dauer (MVCC — Publish stört laufende Leser nicht).

## 8. Frontend

React/Vite-SPA ([../app/frontend/src](../app/frontend/src)) → Build nach `app/ui_static/`,
von [main.py](../app/main.py) als Static Files + SPA-Fallback serviert. Reine Admin-UI
(Dashboard, Dokumente, Graph, Keys, System, Wartung) — **keine Suchseite** (Suche via MCP).

## 9. Selbst-Pflege (Maintenance)

Nächtlicher Pass ([maintenance/](../app/maintenance/)): Tag-Synonyme autonom mergen
(niedrigrisiko, mit Undo-Log), Ordner-Verschiebungen/Duplikate als bestätigungspflichtige
Vorschläge, Graph-Rebuild. Keine stillen Änderungen — jeder Lauf loggt + ist UI-sichtbar.
