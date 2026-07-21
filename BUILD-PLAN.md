# RAG-OS Lokal — Ausführungsplan (final)

> Konsolidierte, ausführbare Bauanleitung. Ergänzt `SPEC.md` (das *Was/Warum*) um das *Wie*.
> Fundament (M0 + Tool-Stack + Kern-Kette) ist **empirisch verifiziert** (siehe §2).
> Stand: 2026-07-20 · Ziel-Python: **3.14** (Schreiber + Leser, verifiziert).
> **✅ M1–M8 KOMPLETT. Beide Windows-Installer gebaut & Payload-Boot verifiziert** (auf `main` `c5ffd9b`).

---

## Fortschritt (Umsetzung)

| Meilenstein | Stand |
|---|---|
| **M1 — Config & Boot** | ✅ **fertig & verifiziert** — `config.py` lokales Profil; `settings()` lädt ohne Docker/Env; Pfade (Vault/`lancedb_uri`/`appstate_db_url`) korrekt. |
| **M2 — Datenhaltung** | ✅ **DB-Layer fertig & verifiziert** — `models.py` Postgres→SQLite portiert (Uuid/JSON/uuid4); `session.py` aiosqlite + WAL/busy_timeout/FK-Pragma, ohne Postgres-Migrationen; `create_all` + `ensure_admin_user` grün (15 Tabellen). **Inkrementell:** vorerst ALLE Tabellen in einer SQLite (lauffähiger Zwischenstand); Korpus/Chunks/Graph wandern in **M3** nach LanceDB, dann bleibt nur `appstate.sqlite`. Offen: In-Process-Job-Status (mit M6). |
| **M3 — Store-Adapter** | ✅ **fertig & verifiziert (Kern + Rest)** — neu `pipelines/store.py` (LanceDB: write/hybrid/`norm_id`-WHERE/delete/scan/**`update_folder`**, explizites Arrow-Schema modellunabhängig) + `pipelines/doc.py` (`RetrievedDoc`); `factory.py` neu (fastembed/ONNX + Shims, **kein haystack/qdrant/ollama**); `query.py`+`reranker.py` umgezogen. **M3-Rest umgesetzt:** `ingest/pipeline._embed_and_store` → `factory.embed_texts` + `store.write` (dense-only, FTS statt Sparse); `vector_ops` delete/move über `store` (Qdrant raus); `api/documents.get_document_chunks` + `api/suggest._store_snippet` über `store.filter_by_meta`; `graph/l2._similar_pairs` über `store.scan_dense_vectors`; `main._warmup_models` → `warmup_embedder`. **E2E grün** (8/8: write→hybrid→ACL→norm_id→scan→update_folder→delete), `ruff check app` grün. **Bewusst offen:** LLM-Shims `get_generator` (autotag/metadata_extract/folder_suggester/reorg) → **M5**; `backup/engine.py` (pg_dump/Qdrant-Snapshot) → **M7** (entfällt/ersetzt). |
| **M4** | ✅ **fertig** — Embeddings über fastembed/ONNX (`factory.embed_query/embed_texts`). **Prod-Modell `intfloat/multilingual-e5-large`** (1024-dim, mehrsprachig) + e5-Query/Passage-Präfixe — beim echten Build entdeckt, dass fastembed **bge-m3 nicht** unterstützt (Test nutzt `bge-small`). |
| **M5 — LLM-freies Tagging** | ✅ **fertig & verifiziert** — **kein LLM/Ollama mehr.** `metadata_extract.py` deterministisch: Eigen-Identität = dominante Norm im Kopf (`graph_refs.norm_matches` + `canonical`), `norm_id` OHNE Jahr (fassungsstabil → Supersede-Key) + `doc_version`/`issued_date` (inline **oder** Kopf-Datum), `doc_type`/`issuer` per Regel, `language` de/en-Heuristik. `autotag.py` deterministisch (Issuer-/Norm-Familie + TF-Keywords). `folder_suggester.py` deterministisch (Norm-Familie→`/Normen/…/`, sonst Top-Tag). `reorg.py` LLM-Namensgebung raus (`_deterministic_name` bleibt). `factory.get_generator` entfernt. `graph_refs.NORM_RE`/`norm_matches` öffentlich (kein Regex-Duplikat). **E2E grün** (15/15: Eigen-`norm_id`, Fassungs-Stabilität, deterministische Tags 2×identisch, Nicht-Norm, folder_suggester), `ruff` grün, **kein Netzwerk-Call**. **Bewusst offen:** „compute-in-RAM/write-once" für `graph/analyze.py`+`l2.py` — **moot solange die Graph-Tabellen in SQLite liegen** (Single-Writer, executemany-UPDATE ok); relevant erst bei Graph-in-LanceDB → **M7**. |
| **M6 — Ingest-Topologie** | ✅ **Queue + Watcher fertig & verifiziert** — `ingest/queue.py` Postgres→**SQLite/ORM in-process** (kein `SKIP LOCKED`/`now()`/`FILTER`/`STRING_AGG`; Single-Writer, `_pick_one`/`_set_status`/`get_job_status` Python-seitig aggregiert). `ingest/watcher.py` `Observer`→**`PollingObserver`** (SMB-/lokal-robust; LanceDB-`_lock` serialisiert Watcher+Queue → Single-Writer bleibt). Config-Default `ingest_backend="docling"` steht. **E2E grün** (8/8: enqueue→running→3 verarbeitet[2 done/1 failed=partial]→Staging-Cleanup→Watcher erkennt lokale Datei + dispatcht Ordner), `ruff` grün. **Bewusst offen (Schreiber-Umgebung/M8-Packaging):** echter Docling+OCR-Parse (Tabellen-/Scan-Treue) + Offline-Modell-Bundling (`DOCLING_ARTIFACTS_PATH`/`HF_HUB_OFFLINE`, bge-m3-Tokenizer) — Adapter (`docling_ingest.py`) unverändert & im RAG_OS-WSL-Lauf bereits bewiesen (Tabelle erhalten); die Modelle (~GB) gehören in den Voll-Installer (M8). |
| **M7 — Publish/Versionen** | ✅ **Kern fertig & verifiziert** — neu `pipelines/publish.py`: `publish()` = atomares Tag-Rolling (`current`←latest, `prev`←alt-current, exakt, kein optimize); `prune_versions()` = Kompaktierung + best-effort-Cleanup (getaggte HART geschützt, Fehler abgefangen, zieht `current` auf kompaktierte Version nach); `checkout_current()` (MVCC-Pin) + `sync_reader_cache()` (Vault→lokaler Cache, rename-Swap; SMB nur Transport). Config `publish_cleanup_grace_days` + `reader_cache_uri`. `backup/engine.py` **pg_dump/Qdrant/OAuth-Cleanup raus** → `backup_vault_index()` (LanceDB-Dataset-Kopie) + `backup_appstate()` (SQLite-Backup-API, WAL-sicher) + publish/prune; Query-Log-Retention bleibt. **E2E grün** (14/14: publish-Tags, **MVCC-Leser-Isolation** [gepinnter Leser unbeeinflusst von Publish, sieht nach checkout_latest alles], Leser-Cache lesbar/lokal, prune schützt Tags ohne Crash, Backup Index+appstate-Snapshot öffenbar), `ruff` grün. **Bewusst offen (deine NAS/M8):** Direkt-auf-NAS-Build + Publish-Race **über echtes SMB**; Leser-*Rolle* (Store öffnet standardmäßig den Cache am Tag) → Installer-Rollen **M8**. Rebuild-aus-Docs (`reindex_all`) existiert. |
| **M8e — Reader/Writer-Rollen-Split** | ✅ **fertig & verifiziert** — P0 (pyproject dreigeteilt base/writer/dev, ui_static-Build-Skript, File-Logging, Config `RAG_VAULT_PATH`/`RAG_SERVICE_ROLE`-Aliase + `app-settings.json` via `appsettings.py`). Ingest-Importe lazy (`watcher.py`, `main.py`-Lifespan, `api/suggest.py`); `store.py` Reader liest lokalen Cache an `current`-Tag, Writer den Vault; Lifespan startet Writer-Tasks (Queue/Watcher/Maintenance/Backup) bzw. Reader-Cache-Refresh (`publish.refresh_reader_cache`). **Verifiziert:** Reader importiert `main` OHNE docling/torch/fitz/magic (Import-Graph-Leck-Test); Writer publisht → Reader synchronisiert Cache → liest an `current` (3/3); `ruff` grün. |
| **M8 — Shell/MCP/Frontend/Packaging** | ✅ **KOMPLETT — beide Installer gebaut & boot-verifiziert** — `mcp_server/server.py`: **Bearer-only** (OAuth-Branch/`_OAuthPrincipal`/`_resolve_oauth_principal` raus), **read-only** (`rag_upload` + TOTP-Gate `_require_mcp_admin_totp` entfernt), neu **`rag_overview`** (kompakte ACL-scoped Bestands-Karte) + **`norm_lookup`** (kanonische `norm_id`, trennt Geschwister-Normen), `rag_get_document` um **Volltext** (Child-Chunks reassembliert, verbatim) erweitert. **Verifiziert** (9/9: 6 read-only-Tools gelistet, `rag_upload`/OAuth/TOTP-Symbole weg, `build_mcp_app` importiert sauber), `ruff` grün. **(a) Tote Datei-Löschung fertig & verifiziert:** `mcp_server/oauth.py`+`oauth_routes.py`, `auth/totp.py`, `worker.py` **gelöscht**; alle Referenzen entfernt (main.py Mount/Branch, `mcp_server/__init__`, `api/auth_router.py` TOTP-Endpunkte, `api/users.py` `totp_enabled`, `db/models.py` OAuth-Tabellen + TOTP-Spalten, `config.py` oauth-Settings + `mcp_admin_email`). Smoke-Test grün (keine OAuth-Tabellen/TOTP-Spalten, alle Router importieren, `init_db`/`create_all` baut ohne oauth/totp, MCP 6 Tools), `ruff` grün. **(b) Frontend-Login-Bypass fertig & verifiziert:** lokale Auto-Admin-Session (`config.local_ui_autologin` + `auth/dependencies.py`), `useAuth` Auto-Login + `AppShell` `ready`-Gate, TOTP-Reste getrimmt; `npm run build` grün. **(c) pywebview-Shell CODE fertig & headless-verifiziert:** neu `app/desktop.py` (uvicorn-Thread @127.0.0.1 + WebView2-Fenster + Readiness-Poll + Vault-Picker + **Tray + Autostart(Registry) + Toast + Drag&Drop-JS-Bridge** + Minimize-to-Tray + sauberes Beenden; GUI-Libs lazy → `py_compile`/Import/Helfer grün, Reader-Guard grün). **(d) Packaging fertig & GEBAUT:** `build/` (`make-icon.py`, `fetch-models.py` [e5-large+Reranker], `ragos-writer.spec`/`ragos-reader.spec`, `installer-writer.iss`/`installer-reader.iss`, `build.ps1`). **Echter Windows-Build durchgeführt:** beide Installer erzeugt (`dist/RAG-OS-Schreiber-Setup.exe` ~3 GB, `dist/RAG-OS-Leser-Setup.exe` ~2,5 GB, Inno „Successful compile") und **beide Payloads booten live** (Health 200, sqlite+lancedb; Writer startet Queue/Maintenance/Backup; Reader liest Cache). **Beim Build gefixte reale Bugs:** e5-large statt bge-m3, `aiosqlite`/uvicorn/SQLAlchemy-`collect_all` (sonst Import-Crash der eingefrorenen App), torch-Lizenzbäume-Purge (`Remove-DistInfoLicenses` in `build.ps1`), ASCII-Prints. **Rest (nur interaktiv):** WebView2-Fenster-Rendering im echten Fenster. **M8f (2026-07-21):** Docling-First-Run-Download war fragil (erster Ingest rannte mit dem HF-Download um die Wette → „Missing safe tensors file") → **Docling Layout/TableFormer + e5-Tokenizer jetzt gebündelt** (`fetch-models.py`, Installer → `models/{docling,e5-tokenizer}`, `artifacts_path` + `HF_HUB_OFFLINE` am Prozessstart). Kein Runtime-Download, air-gapped; Reader excludet sie. |

Verifikations-venv: `scratchpad/m0-spike/.venv` (Python 3.14) — wächst mit den Meilensteinen.
Voller App-Boot (`main.py`) braucht **M3** (Store: Qdrant→LanceDB) + **M4** (ONNX-Embeddings), weil `main.py` die Retrieval-/Factory-Kette beim Import zieht.

---

## 0. Ziel in einem Satz

RAG-OS (VPS-Docker-Stack) wird eine **native, Docker-freie Windows-App**: ein **verbatim-treuer
Normen-/Richtlinien-Wissensspeicher**, den KI-Clients (Claude Desktop u. a.) über **MCP** anzapfen.

---

## 1. Endarchitektur

```
   KI-CLIENT (Claude Desktop …)                     KI-CLIENT (Claude Desktop …)
        │ MCP @127.0.0.1 (Bearer-Key)                    │ MCP @127.0.0.1
   ┌────▼─────────────────────────────┐          ┌───────▼──────────────────────┐
   │ SCHREIBER  (1 uvicorn-Prozess)    │          │ LESER ×N  (schlanker Prozess) │
   │ • Docling+OCR → Chunking          │          │ • nur Abfrage                 │
   │ • ONNX bge-m3 (Embeddings)        │          │ • ONNX bge-m3 + Reranker      │
   │ • LLM-freies Tagging (graphify-   │          │ • liest lokalen Cache         │
   │   inspiriert) + Graph (RAM→1×)    │          │ • KEIN Docling/LLM            │
   │ • schreibt neue LanceDB-Version   │          │                               │
   │ • appstate.sqlite (lokal)         │          │ • appstate.sqlite (lokal)     │
   └──────────┬────────────────────────┘          └──────────▲────────────────────┘
              │ tag "current" (atomar)                        │ checkout(tag) → lokaler Cache
              ▼                                               │
   ┌───────────────────────────────────────────────────────────────────┐
   │ NAS — VAULT (ein portabler Ordner)                                  │
   │   Dokumente/            ← Roh-Dateien (PDF/DOCX/MD), unverändert     │
   │   .ragos/index.lance/   ← LanceDB: EINZIGER Wissensspeicher          │
   │        (chunks: text+vektor+metadaten+norm_id+tags · graph-tabellen)│
   │        native Versionierung + Tags                                  │
   └───────────────────────────────────────────────────────────────────┘
   Regel: nur Schreiber schreibt · Leser lesen lokalen Cache · SMB nur Transport
```

**Kern-Prinzipien**
- **LanceDB = der einzige Wissensspeicher** (Chunks + Vektoren + FTS + Metadaten + Graph). Ersetzt Qdrant **und** die Korpus-Tabellen von Postgres.
- **`appstate.sqlite`** pro Rechner (lokal, NICHT im Vault): `api_keys`, `ui_users`, `query_log`, In-Process-Job-Status.
- **Nebenläufigkeit** über LanceDBs **native Versionierung + Tags** (verifiziert: Cleanup schützt getaggte Versionen hart). Ein Schreiber, viele Leser, lokaler Cache.
- **Kein Docker, kein Server, kein Ollama/LLM.** Schreiber = ein Prozess. Verbatim-Treue (keine LLM-Paraphrase).
- **Retrieval**: Hybrid (dense bge-m3 + FTS/BM25 + RRF) + exaktes `norm_id`-WHERE + Norm-Referenz-Fastpath + Reranker.
- **Tagging/Graph**: deterministisch, LLM-frei, graphify-inspiriert (Norm-Refs, Keywords, Louvain-Communities, God-Nodes, MinHash-Dedup).

---

## 2. Bereits verifiziert (nicht erneut prüfen)

- **M0-Gate GRÜN** (`spike/m0_lancedb.py`): Hybrid + `norm_id` + Versionierung + Publish/Cache in-process.
- **Tool-Stack** (3 Subagenten, 2026-07-18): torch 2.13/docling/rapidocr/pywebview/PyInstaller haben cp314-Wheels → **eine 3.14-Umgebung genügt**. Kern-Kette spielt E2E zusammen. LanceDB-Tags-API real (`table.tags.create/list/get_version`, `checkout(tag)`, `read_consistency_interval`).
- **Graph-Nutzen** (`spike/graph_quality.py`): Norm-Referenz-Fastpath recovert Zitierer, die reines Hybrid bei kleinem k verpasst; schwere PPR-Analytik marginal.

---

## 3. Konventionen (durchgängig)

- **Ziel-Python 3.14**, ein venv. Konfig nur über `settings()` (kein `os.environ`). Strukturiertes Logging (`log.info("event", key=val)`). Blockierende Calls in `asyncio.to_thread`.
- **FTS neu**: `create_index("text", config=FTS(with_position=True, ascii_folding=True, remove_stop_words=False))` (nicht das deprecated `create_fts_index`).
- **Reranker-Import**: `from lancedb.rerankers import RRFReranker`. **Konsole**: `sys.stdout.reconfigure(encoding="utf-8")`.
- **Nichts committen/pushen** ohne Anweisung. Verifikation je Meilenstein manuell + `ruff check app`.
- Repo = Fork von RAG_OS (bereits ins Projekt kopiert). Remote `rag_os_app_lokal` erst auf Anweisung.

---

## 4. Meilensteine (ausführbar)

### M1 — Lokales Config-Profil & Boot
**Ziel:** App bootet lokal ohne Docker/Env-Zwang.
- [ ] `app/config.py`: Settings ergänzen — `vault_path`, `lancedb_path=<vault>/.ragos/index.lance`, `appstate_db=%LOCALAPPDATA%\RAG-OS\appstate.sqlite`, `rag_domain="localhost"`, `oauth_enabled=false`. `embed_model="BAAI/bge-m3"` (ONNX).
- [ ] Pflichtfelder ohne Default entschärfen/entfernen (`postgres_password`, `qdrant_api_key`, `app_secret_key`, `admin_*` → Defaults oder generiert).
- [ ] `<vault>/.ragos/config.json`: Rollen (Schreiber/Leser) + Norm-Muster (ÖNORM/DIN/EN/ISO/OIB/§).
- **Verifikation:** `python -m app` bootet ohne Docker/Env-Vars; legt `.ragos/` + `appstate.sqlite` an.

### M2 — Datenhaltung: LanceDB + appstate.sqlite
**Ziel:** zwei klar getrennte Speicher, sauber getrennt.
- [ ] **LanceDB-Schema** (`app/pipelines/store.py`, neu): `chunks`-Tabelle — `text`, `vector`(1024, float32), `doc_id`, `file_name`, `folder`, `norm_id`, `doc_type`, `doc_version`, `valid_status`, `superseded_by`, `tags`(list), `page`, `section_path`, `is_norm`. Graph: `graph_nodes/edges/communities` (write-once pro Version). „documents"-Sicht = distinct `doc_id`.
- [ ] `app/db/models.py`: **nur noch** appstate — `ApiKey`, `UiUser`, `QueryLog`. `postgresql.UUID`→`sqlalchemy.Uuid`, `ARRAY`→`JSON`, `server_default=gen_random_uuid()`→`default=uuid.uuid4`. `QueryLog.retrieved_doc_ids` als `list[str]`. Korpus-/Graph-Tabellen hier **entfernen**.
- [ ] `app/db/session.py`: `aiosqlite` auf `appstate.sqlite`; `connect_args`/`connect`-Event mit `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`; Pool `NullPool`. Postgres-DO-Block-Migrationen + pgcrypto + GIN **raus** (frisch = `create_all`).
- [ ] **In-Process-Job-Status** statt `ingest_queue`/`ingest_jobs`; `get_job_status` (`documents.py:418`) liest ihn.
- **Verifikation:** frischer Start legt beide Speicher an; `ensure_admin_user`/Key-Create/Query-Log schreiben in appstate; Upload → `chunks`-Zeilen; keine „database is locked".

### M3 — Store-Adapter: Qdrant → LanceDB (alle Call-Sites)
**Ziel:** ein Port kapselt LanceDB; kein Qdrant/Haystack mehr.
- [ ] **`app/pipelines/store.py`**: Port — `search_hybrid`, `filter_by_meta`(→WHERE), `write`, `delete_by_doc_id`, `scan_dense_vectors`, `count`, `health`, `list_documents`, `get_document`. Direkte `lancedb`-API (Haystack raus).
- [ ] Call-Sites umstellen:
  - `pipelines/query.py`: `_retrieve_*_inner` (LanceDB-Hybrid), `_build_access_filter`→WHERE, `_annotate_status` liest aus LanceDB-Zeile, **PPR-Block raus** (`:425-441`), Norm-Fastpath bleibt.
  - `ingest/pipeline.py`: `_embed_and_store` → ONNX-Embed + LanceDB-`write` + `optimize()`.
  - `pipelines/vector_ops.py`: `delete_by_doc_id`; Ordner/Tag-Edit = neue Version (kein in-place `set_payload`).
  - `graph/l2.py`: `scan_dense_vectors`; `api/documents.py:689`, `api/suggest.py:133,381`, `api/system.py:34,54` über den Port; `backup/engine.py` + `factory.enable_quantization` **entfallen**.
  - `auth/folders.py` distinct folder → LanceDB-Scan; `graph/store.py`-Load → LanceDB.
- [ ] Haystack + `qdrant_client` + fastembed-Sparse-Importe entfernen.
- **Verifikation:** Retrieval qualitativ ok (Hybrid + `norm_id` + Fastpath); Ordner verschieben ohne Vektor-Neuberechnung; Delete per `doc_id` wirksam.

### M4 — Embeddings: ONNX bge-m3 überall
**Ziel:** dense Embedding via fastembed/ONNX, kein Ollama.
- [ ] `pipelines/factory.py`: `get_text_embedder`/`get_embedder` → `fastembed.TextEmbedding("BAAI/bge-m3")` (1024-dim). `OllamaTextEmbedder`/`OllamaDocumentEmbedder` entfernen.
- **Verifikation:** Leser ohne Ollama liefert Hybrid-Treffer; Embed-Dim = LanceDB-Schema.

### M5 — LLM-freies, graphify-inspiriertes Tagging + Enrichment (nur Schreiber)
**Ziel:** kein Ollama; deterministische Tags + Graph.
- [ ] Ersetze `ingest/autotag.py` + `ingest/metadata_extract.py` (qwen) durch deterministische Anreicherung: **Tags** = Issuer (ÖNORM/DIN/EN/ISO/OIB/§) · Norm-Familie · Ordner · **Keyword-Extraktion** (TF-IDF-artig) · Community-Label.
- [ ] **Eigen-Identität ohne LLM** (blockierend): doc-eigenes `norm_id` aus kopf-/titelnaher Norm (dominante/erste Norm im Kopf), `doc_version`/Jahr aus Dateiname/Docling-Metadaten/Norm-Suffix → hält Supersede/`only_current` (`pipeline.py:416-451`, `build.py:114-120`). `language` via Docling `lang_detect` (`docling_ingest.py:30`) oder Feld streichen.
- [ ] **Referenz-Kanten**: `ingest/graph_refs.extract_refs` + `graph/canonical` → `references`-Kanten mit `EXTRACTED`/`INFERRED` (Vorbild graphify `extractors/markdown.py`). Speisen `norm_lookup` + Query-Fastpath.
- [ ] **LLM-freie Analytik** — `graph/analyze.py` (Louvain/God-Nodes, schon LLM-frei) + `graph/l2.py` (MinHash): auf **compute-in-RAM, write-once** umstellen (kein executemany-UPDATE).
- [ ] **LLM-Kopplung entfernen**: `services/folder_suggester.py:91` (`get_generator`, via `api/suggest.py:35`) deterministisch ersetzen/streichen; `graph/reorg.py:38` Top-Level-Import + `_llm_folder_name` raus (`_deterministic_name` bleibt); `factory.get_generator` entfernen.
- [ ] Human-in-the-loop: Tags = Vorschläge, in der UI prüf-/korrigierbar.
- **Verifikation:** Ingest eines Docs → deterministische Tags + Norm-Refs + Community, **kein LLM-Call**; Supersede/`only_current` funktioniert.

### M6 — Docling + OCR Ingest + Import-Wege
**Ziel:** layout-/tabellen-/OCR-treues Parsing; bequemer Import.
- [ ] `ingest_backend="docling"` als Default; Docling-/OCR-/bge-m3-Tokenizer-Modelle mitliefern bzw. First-Run-Download (`HF_HUB_OFFLINE`-sicher, Vorbild `Dockerfile.ingest:39-42`).
- [ ] **Überwachungsordner LOKAL beim Schreiber** (`ingest/watcher.py` → `PollingObserver`, SMB-Events unzuverlässig) + Ordner-Batch + Drag&Drop. In-Process-Ingest-Task. Versionierung bei Re-Upload.
- **Verifikation:** gescanntes PDF mit Tabelle → korrekter Text + erhaltene Tabelle; Datei in Überwachungsordner → auto-ingestet; `norm_lookup` findet die Norm exakt.

### M7 — Publish/Versionen (LanceDB nativ) + Cache + Backup
**Ziel:** Schreiber veröffentlicht immutable Versionen; Leser cachen lokal.
- [ ] Schreiber schreibt neue LanceDB-Version direkt aufs NAS-Dataset (append/merge; MVCC); am Ende `table.tags.create("current", <version>)` = atomar veröffentlichen. Fallback bei SMB-Build-Zicken: lokal bauen, Dataset-Verzeichnis kopieren.
- [ ] Leser: `checkout("current")` bzw. `read_consistency_interval`; **lokaler Cache** (temp → verify → atomarer Dir-Swap), Versions-Handle für Query-Dauer pinnen. SMB nur Transport.
- [ ] **Retention K=2**: „current" + 1 Vorgänger getaggt (Cleanup schützt getaggte hart), Rest via `optimize(cleanup_older_than=…)`/`cleanup_old_versions`.
- [ ] **Backup**: `backup/engine.py` (pg_dump/Qdrant) **entfällt** → Vault-Ordner-Kopie + NAS-Snapshots + Rebuild-aus-Docs.
- **Verifikation:** Schreiber veröffentlicht v2 → Leser sieht sie nach Refresh; Publish während Leser-Query crasht nicht; Rebuild-aus-Docs stellt den Index her.

### M8 — Native Windows-Shell + MCP + Frontend + Packaging
**Ziel:** doppelklickbare App, zwei Installer.
- [ ] **Shell**: `pywebview` (WebView2) + `uvicorn` im Thread auf `127.0.0.1`; Tray + Autostart + Toast + Drag&Drop. Import-Zeit-Nebenwirkungen von `main.py:70-71` (build_mcp_app zieht den Store) mit M3/M4 auflösen.
- [ ] **MCP** (`mcp_server/server.py`): OAuth-Pfad aus, Bearer behalten; **`rag_upload` entfernen**; `rag_get_document` um **Volltext** (Chunks reassemblieren); **`rag_overview`** (kompakte Bestands-Karte aus LanceDB); `norm_lookup` (via `norm_id`).
- [ ] **Frontend** (`app/frontend`): Login lokal entschärfen (Auto-Login/Bypass; `AppShell`-Redirect `:12-15`; `client.ts` 401→`/login` `:21`); Users/TOTP-Seite trimmen; Upload/Drag&Drop.
- [ ] **Toten Code löschen**: `mcp_server/oauth*.py`, `auth/totp.py` (+ TOTP-Refs), `worker.py`, `backup/engine.py`-Custom.
- [ ] **Packaging**: PyInstaller one-dir ×2 (Voll = + Docling/torch/OCR; Leser-schlank = nur Query) → Inno Setup. Fallstricke: WebView2-Bootstrapper, `onnxruntime` Hidden-Imports + Modelle, LanceDB native `.pyd`-Hook, torch `collect-all` (Voll), Docling-Modell-Bake. Eine 3.14-Umgebung.
- **Verifikation:** beide Installer auf sauberem Windows-Profil; Claude Desktop → `rag_retrieve` → Chunks; lokale UI ohne Login-Reibung.

---

## 5. Querschnitt

**Wird gelöscht** (permanenter Fork): OAuth/2FA/TOTP (`mcp_server/oauth*`, `auth/totp`), `worker.py` + Queue-`SKIP LOCKED`, `backup/engine.py`-Custom, Ollama-Generator (`factory.get_generator`, `autotag`, `metadata_extract`), PPR-Query-Augmentierung + LLM-Reorg-Naming, Haystack/`qdrant_client`, Docker-/Compose-/Caddy-Dateien.

**Modelle** (alle ONNX/lokal, cp314): bge-m3 (dense, 1024, mehrsprachig/DE) · bge-reranker-v2-m3 (Rerank) · Docling-Layout/Tabelle + RapidOCR (nur Schreiber). **Kein qwen/Ollama.**

**Zwei Installer**: Voll (Schreiber, ~10–15 GB Modelle) · Leser-schlank (Query, ~2–3 GB). Vault + Versionen leben auf der NAS; lokal nur Modelle + ein Query-Cache.

---

## 6. Kritische Dateien

`app/config.py` · `app/db/{models,session}.py` · **neu** `app/pipelines/store.py` · `app/pipelines/{query,vector_ops,factory,reranker}.py` · `app/ingest/{pipeline,graph_refs,watcher,docling_ingest}.py` · `app/graph/{canonical,analyze,l2,build,store,reorg}.py` · `app/services/folder_suggester.py` · `app/api/{documents,suggest,system}.py` · `app/mcp_server/server.py` · `app/main.py` · `app/frontend/src/{hooks/useAuth.ts,api/client.ts,components/layout/AppShell.tsx,pages/Login.tsx}` · **neu**: Publish-/Cache-Schicht, Norm-Regex-Register, Packaging (2× PyInstaller-Spec + Inno-Setup).

---

## 7. Offene Kleinpunkte / Rest-Risiken

- torch/docling per Wheel+Dry-Run belegt, **voller Laufzeit-Import** steht beim ersten echten Build aus (~2–3 GB).
- Direkt-auf-NAS-**Build über echtes SMB** (Tempo/Robustheit) erst auf Julius' NAS final testbar → Fallback „lokal bauen + kopieren" steht.
- `language`-Quelle (Docling `lang_detect` vs. Feld streichen) beim Bau entscheiden.
- Optionaler LLM-Anreicherungs-Pass (à la graphify `llm.py`) bewusst zurückgestellt, später nachrüstbar.

---

**Reihenfolge:** M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8. Nach jedem Meilenstein die genannte Verifikation + `ruff check app`, bevor der nächste startet.
