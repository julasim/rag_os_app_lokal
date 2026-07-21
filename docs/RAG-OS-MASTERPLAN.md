# RAG-OS — Endgültiger Umbauplan (2026-07-15, review-korrigiert)

> ✅ **ERLEDIGT / HISTORISCH (Stand 2026-07-21).** Dieser Plan (Tracks C–F, noch in
> der Docker-Welt gedacht) ist umgesetzt. Der **Docker-freie native Umbau (M1–M8, + M8f
> Modell-Bündelung)** ist fertig, gebaut und E2E-verifiziert. Lebender Fortschritt +
> Architektur: **`BUILD-PLAN.md`** und **`CLAUDE.md`**. Dieses Dokument bleibt als
> Aufzeichnung der Planungsphase.

> Drei Subagenten haben Plan **und echten Code** geprüft (Sicherheit · Korrektheit/
> Funktionalität · Effizienz/Algorithmen). Ihre Befunde sind hier eingearbeitet.
> **Gesamturteil:** Architektur tragfähig und mit dem realen Code konsistent; die
> Kern-Seams sitzen dort, wo der Plan sie verortet. **Vor Umsetzung sind mehrere
> kritische Fixes zwingend** (per-User-ACL fail-safe, REST-Rollen-Gating, Split-Brain-
> Move, Rollen-Constraint, LSH-Schwelle, scipy + Graph-Boot-Load, Latenz-Neumessung).
> Reihenfolge **C→D→E→F** ist zirkelfrei; riskantester Track = **C** (C0-Spike als Gate).

---

## 0. Zielarchitektur (von allen 3 Reviews bestätigt)
- **rag-api** (Serving, `proxy`+`default`-Netz): FastAPI `/api` (Verwaltung) + `/mcp`
  (Suche, read-only) + React-UI. **Schlank:** ONNX-Reranker, **kein torch/Docling**.
- **rag-ingest** (Worker, nur `default`-Netz, `container_name ≠ rag-api`): Docling+torch;
  parst/chunkt/embeddet **und baut den Graph**. Konsumiert die bestehende Ingest-Queue
  (`FOR UPDATE SKIP LOCKED` → race-frei, im Code bestätigt).
- **postgres** = Source-of-Truth (Document, **DocumentChunk** neu, ui_users, OAuth*,
  QueryLog, **GraphNode/GraphEdge/GraphCommunity** neu, folder_suggestions neu).
- **qdrant** = abgeleiteter Vektor-Index (dense bge-m3 + BM25-sparse, INT8-quant).
- **ollama** = Embeddings + Ingest-LLM (Qwen 3B). Nur intern.
- **Prinzip:** Postgres=Wahrheit, Qdrant=Index, Graph=Beziehungen (beide aus Postgres
  reproduzierbar). Serving schlank/schnell, Ingest schwer/async — getrennt.
- **Rollenteilung:** RAG **beschafft & verlinkt** Evidenz, das KI-OS/der Client **urteilt**.

---

## 1. Zustandsanalyse — bestätigte Defekte (im Code verifiziert)
1. **DOCX-Tabellen verworfen** — `parsers.py:_parse_docx` liest nur `word.paragraphs`,
   nie `word.tables` (stiller Datenverlust). PDF nutzt `find_tables()`.
2. **Reranker ungekappt** — `reranker.py` `CrossEncoder(_MODEL)` ohne `max_length`;
   Chunks bis 2800 Zeichen (`chunker.py`, size=700 × 4).
3. **`language`-Filter löchrig** — `query.py` exact-match, `language=null`-Docs fallen raus.
4. **`reindex_all` reihenfolgeabhängige Ablöse-Heuristik** (CLAUDE §14).
5. **Zwei getrennte Qwen-Calls pro Ingest** (autotag + metadata).
6. **NEU aus Review — realer Split-Brain-Bug:** `patch_document` ([api/documents.py](app/api/documents.py):559)
   setzt `folder_path` in Postgres, **ohne Qdrant-Sync** → verschobenes Dokument wird
   unauffindbar (`meta.folder`-Mismatch). Muss die Track-F-Move-Funktion mitheilen.
- Offene Punkte (Bestand): keine Test-Suite, at-rest-Verschlüsselung, `OLLAMA_NUM_PARALLEL`,
  Offsite-Autotrigger, Formate (pptx/csv/Bilder), Connectors, Eval-Gold-Set, Doku-Drift.

---

## Track C — doc_ingest (layout-aware Parsing/Chunking) — Fundament
**Riskantester Track, Gate für D & F.** `doc_ingest` existiert nur als SPEC
(`libs/doc-ingest/SPEC.md`) → als Paket `app/doc_ingest/` (importierbar + CLI) **im Repo** bauen.

- **C0 — Feasibility-Spike (WSL, GATE):** Docling+Deps offline im slim-Image installieren,
  ein PDF **und ein DOCX-mit-Tabelle** parsen; Footprint/Parse-Latenz/RAM messen.
  **Docling ~2–5 s/Seite auf CPU** (tabellenlastig oberes Ende) → 50-Seiten-PDF ~2–4 min;
  Bestands-Erst-Ingest 100 Docs ≈ 5 h seriell → **Worker-Parallelität einplanen**. Erst grün → weiter.
  > **C0-ERGEBNIS (2026-07-15): GRÜN** — DOCX-mit-Tabelle (0,06 s) **und** PDF-mit-Tabelle
  > (5,36 s) parsen **offline** (`--network none` + `HF_HUB_OFFLINE=1`), Tabellen als Markdown
  > extrahiert. Image 5,26 GB · Modell-Cache 1,3 GB · Peak RSS 1,09 GB. **Exakte Offline-Regeln
  > für C1/C3:** (a) `torch`+`torchvision` gemeinsam aus CPU-Index (sonst `torchvision::nms`);
  > (b) `docling-tools models download` → `/root/.cache/docling/models`; (c) `PdfPipelineOptions.
  > artifacts_path` auf diesen Pfad **+ `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1`** (sonst lädt
  > Layout/TableFormer zur Laufzeit vom HF-Hub → offline Absturz); (d) `do_ocr=False` für
  > born-digital PDFs (kein RapidOCR/modelscope-Download); gescannte PDFs → RapidOCR-ONNX vorab backen.
- **C1 — Bau (Parsing-Qualität = Kern):** detect (Scan-Textdichte) · router · normalize
  (Docling offline artifacts) · serialize (**MarkdownTableSerializer** + verlustfreies
  `table_html`) · quality (`report.json`, DOCX-Tabellen-Leer-Guard, Header/Footer-Dedup) ·
  chunk (HybridChunker, Tokenizer bge-m3, Parent-Child) · schema (content-basierte IDs).
  **Später (C1b):** Mehrseiten-Tabellen-Stitching (Flag bleibt Kern), TEDS/NID-Eval,
  Lizenz-CI. `easyocr`/`python-bidi`/`python-Levenshtein` ausschließen.
  > **C1-ERGEBNIS (2026-07-15): Kern GEBAUT & im Container verifiziert.** Paket
  > `app/doc_ingest/` (config/convert/chunk/schema/`__init__`/cli). DOCX **und** PDF →
  > **Markdown-Tabelle im Text** (via `MarkdownTableSerializer`) **+ `table_html`**
  > (verlustfrei, über `self_ref`→`dl_doc.tables`); Parent-Child-Records, `section_path`,
  > `page`, content-basierte `chunk_id`. **Pin-Versionen (C3):** docling 2.113.0,
  > docling-core 2.87.0, docling-ibm-models 3.13.3, torch 2.13.0+cpu, torchvision
  > 0.28.0+cpu, transformers 5.13.1, rapidocr 3.9.1. **Offen (C1b/C3):** bge-m3-**Tokenizer**
  > + RapidOCR-ONNX-Modelle in den Vorab-Download backen (sonst Chunker/OCR nicht air-gapped);
  > `py3langid`-Dep; Header/Footer-Dedup; echte Scan-Textdichte-Erkennung (`ocr=auto`).
- **C2 — Integration:** Adapter `app/ingest/docling_ingest.py` am Seam `parse_file`+
  `chunk_document` ([pipeline.py](app/ingest/pipeline.py):250/334). `parsed.full_text` wird
  von **genau 3 Stellen** konsumiert (autotag, metadata, `suggest.py:433`) → aus geordneten
  Child-Texten rekonstruieren. `_embed_and_store` erwartet nur `{text, metadata}` (loser
  Vertrag) → problemlos ableitbar. **Feature-Flag `ingest_backend=legacy|docling`, default
  legacy** bis WSL-grün, Legacy als Rollback.
  > **C2-STAND (2026-07-15): Adapter+Branch GEBAUT & Legacy-E2E verifiziert.** Neu:
  > `app/ingest/docling_ingest.py` (run_docling/docling_full_text/docling_to_chunks),
  > `ingest_backend`-Flag in `config.py`, Backend-Weiche in `pipeline.py` (full_text/
  > file_name/mime_type/chunks). **Isolierter Full-Stack in WSL-22.04** (eigener Docker-
  > Daemon, getrennt von der 24.04-Audit-Umgebung) hochgezogen: api+postgres+qdrant+
  > ollama healthy. **Legacy-Ingest→Retrieve E2E grün** (Upload→indexed, norm_id/doc_type/
  > language via Qwen, MCP `rag_retrieve` Treffer 362 ms) → C2-Branch bricht den Ist-Pfad
  > nicht.
  > **C2b GRÜN (2026-07-15):** `DocumentChunk`-Tabelle (`db/models.py`, via `init_db`/
  > create_all angelegt) + Schreibpfad `_store_document_chunks` (Postgres VOR Qdrant,
  > idempotent, content-basierte `chunk_id`). Verifiziert: DOCX-mit-Tabelle über docling →
  > `document_chunks` = 1 child mit **parent_id + table_html + text** → Postgres = Chunk-
  > Wahrheit, Track D entsperrt. **Offen (bewusst):** reindex_all aus document_chunks
  > (statt Re-Parsen) — Optimierung, nachgezogen mit C3b.
- **C2b — Kanonische Postgres-Chunk-Schicht (`DocumentChunk`):** content-`chunk_id` (PK,
  **stabil & meta-unabhängig**, damit Parent/Child-Refs Moves überleben), `logical_id`,
  `parent_id`, `level`, `ordinal`, `prev/next_id`, `text`, `section_path[]`, `element_types[]`,
  `table_html`, `token_count`, `page`, `folder_path`. Ingest schreibt **erst** Postgres,
  **dann** Qdrant (abgeleitet) → kein Split-Brain. **`reindex_all` aus `document_chunks`
  ⨝ `documents`** (Review-Fix: tags/doc_type/norm_id/language/valid_status leben auf
  `Document` und müssen für den Payload gejoint werden — sonst verliert der Index Filterbarkeit).
- **C3 — Worker/Deploy:** **Alle** In-API-Ingest-Pfade auf **Enqueue** umbauen (Single-Upload
  `documents.py:172`, MCP `rag_upload` `server.py:255`, Watcher `watcher.py:56`) — sonst
  bleibt Docling/torch im Serving-Image nötig. Queue-Worker + Watcher **aus dem API-Lifespan
  lösen** ([main.py](app/main.py)) in den `rag-ingest`-Entrypoint. Compose: neuer Service,
  `deploy.resources.limits.memory` **~4–6 GB** (Docling-Peak, sonst OOM), geteilte Mounts
  `./data/uploads`+`./data/models`, `DOCLING_ARTIFACTS_PATH`, `offline=True`. `torch` nur
  transitiv via `sentence-transformers` → Dep-Schnitt bewusst setzen (ONNX-Reranker in
  rag-api, torch nur im Worker).
  > **C3a-STAND (2026-07-15): docling-Backend E2E GRÜN im echten Stack.** Ingest-Image
  > `rag-ingest` = `FROM sima-rag-api` + torchvision + docling==2.113.0/docling-core==2.87.0
  > (`app/Dockerfile.ingest`, `docker-compose.docling.yml`). Modelle in `./data/models/
  > docling` (Mount, `DOCLING_ARTIFACTS_PATH=/models/docling`). **Beweis:** DOCX-mit-Tabelle
  > → Backend `docling` → indexed → Chunk enthält **Markdown-Tabelle** → MCP `rag_retrieve`
  > Treffer 1.0 **mit Tabelle** (der Legacy-DOCX-Tabellen-Bug ist end-to-end behoben).
  > **Offen (C3b):** echter Worker-Split (Queue/Watcher aus api-Lifespan, alle Ingest-Pfade
  > enqueuen, Serving-Image ohne torch/docling), bge-m3-Tokenizer + offline backen.

---

## Track D — Wissensgraph (geschichtet L1→L2→L3), Multi-Hop
Graph **greenfield** bestätigt (kein networkx im Repo). **Kernentscheidung (alle 3 Reviews):
Analyse/PPR auf Konzept-Ebene** (document/norm/tag/folder/issuer/entity, ~10²–10⁴ Knoten,
networkx in-memory ~100–200 MB), **NIE 10⁶ Chunk-Knoten**. Chunk-`similar_to`/`near_dup`
werden zu Doc↔Doc-Kanten verdichtet.

**Schema (Postgres):** `GraphNode`(id, node_type, label, `canonical_key`, `folder_paths[]`,
pagerank, community_id, participation); `GraphEdge`(src, tgt, relation, `layer`(L1/L2/L3),
`confidence`, `w_eff`); `GraphCommunity`(id, label, conductance, `member_fingerprint`, size).

**L1 — Deterministisch (jetzt):** `references` (Regex ÖNORM/EN/ISO/DIN/§), `supersedes`/
`issued_by`/`is_norm` (aus Metadaten), `has_tag`, `in_folder`/`part_of`, `section_path`.
**Ein `app/graph/canonical.py`** (NFKC→`[^\w]+`→`_`→casefold, idempotent) + Normen-Vor-
Kanonisierung (Version abspalten, `§`→`par`, `B1801`=`B 1801`) — **von L1 UND L3 UND einer
`normalize_norm_id()` für Ingest+Query identisch genutzt** (gegen Ghost-Nodes/Fastpath-Split-
Brain). Kollision: gleicher Key + anderer Typ/disjunkte ACL → **nicht mergen** (`ambiguous`).
> **D-STAND (2026-07-15): `app/graph/canonical.py` gebaut & verifiziert.** `normalize_key`
> (idempotent bewiesen) + `canonical_norm_id` (ÖNORM-Varianten → ein Key, Ausgabejahr nur
> bei `:`/`(`/„Ausgabe" abgetrennt — `EN 1992`/`ISO 9001` bleiben Nummern) + `canonical_legal_ref`
> (`§ 12`→`par_12`, `Art.`/`Artikel` vereinheitlicht). **Nächste D-Schritte:** `GraphNode`/
> `GraphEdge`/`GraphCommunity`-Modelle, `ingest/graph_refs.py` (references-Regex), `graph/build.py`
> (L1-Kanten aus `document_chunks`), dann L2 (kNN+MinHash), dann Retrieval-Fastpath/PPR.

**L2 — Ähnlichkeit (jetzt), Doc-Level:** `similar_to` (Qdrant-kNN Top-N=10, **mutual**,
τ≈0.80 **kalibrieren**) + `near_dup` (**eigene MinHash**, num_perm=128, Wort-Trigramme).
**Review-Fix LSH-Bandung:** `(b,r)=8×16` hat Detektions-Mittelpunkt ~0,88 → bei Jaccard=0,8
nur ~20 % Recall. Für Ziel „Near-Dup ab 0,8" **`(b,r)=16×8`** (Mittelpunkt ~0,71) — oder die
Zielschwelle ehrlich auf ≥0,88 dokumentieren. **Sparsifizierung Pflicht** (Top-k=8/Knoten,
Deckel 0.3) gegen Blob-Kollaps. „related"(0.5) und Chunk-Level **weglassen**.

**L3 — Lokaler-LLM-Entity-Layer (Phase 2, A/B):** **Entity-Linking für Recall, kein
Urteilen** — festes Schema, Validierung/Dedup gegen `canonical_key`. Im rag-ingest-Worker,
async. Modell 3B vs. 7B–14B in WSL benchmarken. Erwartung: L1+L2 führen die Docs oft schon
zusammen → L3 nur scharf, wenn A/B-Messung Mehrwert zeigt.

**Analyse (Nachtlauf):** **Louvain** (networkx BSD, resolution=1.0, seed, max_level;
Determinismus: sortierter Aufbau + ID-Remap + Member-Fingerprint) + Oversize-Split +
Conductance. God-Nodes = **PageRank** + Rausch-Filter (folder aus, Tag-Stoppliste,
Frequenz>30 %). Brücken = **Participation-Coefficient (primär, O(M))**; **Edge-Betweenness
weiter hinten** (O(N·M), nur Nachtlauf ≤10⁴ — Review: liefert nur ~10 % Zusatznutzen).

**Retrieval-Integration** ([query.py](app/pipelines/query.py) `run_retrieve`):
- **Fastpath (Prio 1):** Regex→`canonical_key` O(1)→Doc-Chunks, ACL-gefiltert; reine
  Identitätsfrage → Vektorsuche+Reranker gespart. Kein Hit → stiller Fallback.
- **PPR-Multi-Hop (auto-gated):** Seeds=Hybrid-Top-8, α=0.5, ε=1e-4, Teilgraph r=2/N_max=5000,
  Top-M=10; **Fusion RRF (k=60), Kandidaten-Cap = top_k×3** (Reranker-Last konstant).
- **scipy als Dependency (Review-Fix):** `nx.pagerank` nutzt den scipy-Sparse-Pfad; ohne
  scipy 10–30× langsamer. **`scipy` aufnehmen + PPR im Warmup vorwärmen.**
- **Graph-Boot-Load + Refresh (Review-Fix, neu):** rag-api lädt den Graph **einmal beim
  Boot** aus Postgres in RAM (Warmup), **nie pro Request**. Der Worker bumpt einen
  Version-/`source_hash`-Stempel nach Rebuild; rag-api lädt bei Änderung neu.
- **ACL-Subgraph-Cache (Review-Fix):** induzierten ACL-Teilgraphen **pro ACL-Signatur
  cachen**, nicht pro Request neu bauen. Realistischer Graph-Aufschlag **~50–150 ms**
  (nicht 15–45) inkl. Doc→Chunk-Nachladung — die mit der Hybrid-Kandidatenmenge
  **dedupliziert zusammenführen**, nicht als separate Qdrant-Runde.

**Sicherheit (Graph, mit Review-Fixes):**
- **near_dup-Kollaps: Sichtbarkeit über SCHNITTMENGE mit Caller-ACL, NICHT Union**
  (Review KRITISCH-Fix: Union leakt die Existenz fremder Docs). Ordnerübergreifende
  near_dup/similar-Kanten für beschränkte Caller **droppen**; Brücken mit ≥1 unerlaubtem
  Endpunkt ausfiltern; Communities/`shortest_path`/`neighbors` nur über erlaubte Knoten.
- **Sanitize-on-Serialize über ALLE modell-/dokumentabgeleiteten Felder** (Review-Fix:
  nicht nur Graph-Labels — auch `tags`, `norm_id`, `doc_type`, `section_path`, `citation`,
  `file_name` in `rag_retrieve` **und** Graph-Tools). Chunk-Text bleibt untrusted.
- **Struktur/Inhalt-Trennung + Content-Budget:** Struktur-Tools nur Metadaten; Inhalt nur
  `rag_retrieve` (Query-Pflicht, top_k-gedeckelt). **Content-/Exfil-Budget zählt distinct
  doc_ids aus `list`/`get` mit** (Review-Fix), Schlüssel = `user_id`.

---

## Track E — Mehrbenutzer + Rollen + per-User-ACL (SICHERHEITSKRITISCH, neu entworfen)
> Review-Urteil: in der alten Fassung **noch nicht prod-tragfähig** — zwei strukturelle
> Lücken zuerst schließen.

- **KRITISCH-Fix 1 — Fail-safe-Inversion beseitigen.** Die bestehenden Helfer behandeln
  **leere `allowed_folders` als UNBESCHRÄNKT** (`folders.py:54/76`). Track E braucht
  `leer = nichts`. → **Getrennter Codepfad mit `access_all`-Parameter**: `access_all=True →
  None` (unrestricted); `access_all=False → allowed_folders`, leere Liste **explizit `[]`
  (nichts), nie `None`**. „leer = alles" **nie** wiederverwenden. Betrifft `mcp_server/server.py`,
  `auth/folders.py`, `pipelines/query.py`.
- **KRITISCH-Fix 2 — REST/Web-UI-Rollen-Gating.** `AuthContext.can_access_folder`/`has_scope`
  ([dependencies.py](app/auth/dependencies.py):45-56) geben für **jeden** UiUser `True`. Track E
  darf **nicht nur MCP** verdrahten → sonst loggt sich ein read-only User per Session/
  `X-UI-Token` ein und löscht/exportiert über REST alles. **`AuthContext` muss Rolle +
  per-User-ACL tragen** und für `role=user` einschränken (oder Nicht-Admin-UI-Sessions
  serverseitig sperren).
- **HOCH-Fix 3 — Löschen human-gaten.** Delete-Endpunkte (`documents.py` delete/delete_folder)
  hängen nur an `has_scope("delete")` → Bearer-Key mit delete-Scope löscht über REST. →
  auf **`require_ui_admin`** umstellen **und `delete`-Scope aus API-Keys zurückziehen**.
  `delete_folder` zusätzlich über kanonische ACL statt rohem `LIKE` (MITTEL-Fix 9).
- **HOCH-Fix 4 — Rollen-Enum/Constraint.** Heute `UserRole={ADMIN,VIEWER}` + CheckConstraint
  `role IN ('admin','viewer')` (`models.py:39/78`); Default `ADMIN` (gefährlich). Plan nutzt
  `user`. → **Constraint idempotent auf `('admin','user')` migrieren** (bzw. `viewer`
  beibehalten), **Column-Default auf `user` + `access_all=false`**, `create_user` setzt
  Rolle/ACL **immer explizit**.
- **Spalten-Migration (MITTEL-Fix):** `init_db`/`create_all` **fügt keine Spalten** zu
  bestehenden Tabellen hinzu → explizite `ALTER TABLE ui_users ADD COLUMN IF NOT EXISTS`
  für `role`/`access_all`/`allowed_folders`/`totp_secret`/`totp_enabled` (Muster existiert
  für `documents` in `session.py:86`).
- **Rolle → Scope/ACL:** `user` → Scope `["read"]` + per-User-ACL; `admin` →
  read+write+admin (+ delete nur Web-UI); OAuth-Principal ([server.py](app/mcp_server/server.py)
  `_resolve_oauth_principal`) ersetzt `_AllFolders()`+alle-Scopes durch Rolle+ACL. Bearer-Keys
  behalten eigene ACL.
- **MCP-Admin-Write mit TOTP (HOCH-Fix 5):** `rag_upload` erzwingt **Principal ist der eine
  `mcp_admin` UND gültiges TOTP** (nicht nur `write`-Scope → sonst Bearer-Bypass). TOTP
  **an die konkrete Aktion gebunden** (Hash aus file_path+folder_path+tags), **single-use**,
  **harter Lockout** (5 Fehlversuche → Sperre; `RateLimiter` 10/min zu lasch). Threat-Model
  (Enrollment/verlorenes Gerät/Recovery/±1-Fenster) dokumentieren.
- **Audit (NIEDRIG-Fix 10):** `user_id` (nullable) in `QueryLog` (heute `id=None` für alle
  OAuth-User → keine Attribution). Rate-Limit/Budget/Anomalie auf `user_id` keyen.
- **Ordner-Zugriffs-System (UI):** Admin wählt „Zugriff auf alles" (`access_all=true`) oder
  „Bestimmte Ordner" (Baum-Mehrfachauswahl); **Default neuer User = sieht nichts**.

---

## Track F — Ordnerstruktur-Analyse & Reorg (Button) — **substanzieller Neubau**
> Review-Korrektur: die `folder_suggestions`/Accept-Infrastruktur **existiert nicht**
> (CLAUDE §8 = Absicht, nicht Code; es gibt nur `DuplicateSuggestion` + synchrones
> Per-Doc-Raten in `folder_suggester.py`). Track F ist **Neubau**, nicht „Verdrahtung".

- **Neu:** `folder_suggestions`-Tabelle (pending/accepted, Undo-Payload), Maintenance-
  Verdrahtung (Engine läuft heute nur `consolidate_tags`+`detect_duplicates`), Diff-Review-
  UI, admin-only Trigger.
- **Gemeinsame Move-Funktion (Bugfix + Fundament):** Postgres `folder_path` **und** Qdrant
  `meta.folder`/`meta.folder_path` **atomar**; bei Qdrant-Fehler Postgres zurückrollen bzw.
  „reconcile pending" (Review HOCH: heilt den `patch_document`-Split-Brain **und** die
  nicht-transaktionale `apply_suggestions`). **Alle** Move-Pfade (`patch_document`,
  `apply_suggestions`, Reorg) darauf umstellen. `apply_suggestions` **admin-only + per-Doc-
  ACL** (heute nur `write`-Scope, kein ACL-Check → IDOR-artig).
- **Gruppierung deterministisch** aus Track-D-Communities + Metadaten; **LLM nur optional
  für Ordner-NAMEN**. Ausgabe = Vorschläge (Diff, 1-Klick-Accept, reversibel), **kein
  Auto-Umbau, kein Löschen**. Button + Nachtlauf (Deltas gegen Ist-Struktur).

---

## Track A — Speed (ehrliche Zahlen, Review-korrigiert)
> Review HOCH: die drei Reranker-Zahlen im alten Plan sind **gegenseitig inkonsistent**
> (3787 ms/Paar an ~1500-Zeichen/~300-Token-Chunks liegt **unter** dem 512-Cap → `max_length`
> spart dort 0 %; 3787 ms × 15 Paare ≠ 7,5 s). **Vor dem Commit sauber neu messen** (warm,
> pro Paar UND Batch, reale Chunk-Längen).
- **A0 (kein Re-Index):** `max_length=512` (wirkt nur auf Chunks >512 Token, ~10–20 %),
  `retrieve_k = top_k*3 → *2`, CPU 2→4 (Vorbedingung `nproc`). Realistisch **~10 s → 6–7 s**
  (nicht 2–4 s). `batch_size` ist bei ≤15 Paaren wirkungslos (nicht als „Optimierung" führen).
- **A1 (der eigentliche Sprung):** **Reranker → ONNX-INT8 desselben bge-reranker-v2-m3**
  (einzige DE-taugliche + permissive Option) + **Qdrant Scalar-INT8 + Rescoring** (`on_disk`
  ist schon an; kein Re-Index). **Ziel ~1,5–3 s.** **Headline-Latenz an A1 hängen, nicht A0.**
- **A3-Mikro:** `_log_query` **fire-and-forget** (heute inline awaited, `query.py:271`,
  ~5–15 ms Hotpath); `verify_api_key` Prefix-Index statt O(N)·bcrypt.
- **RAM-Budgets (Review-Fix):** Serving realistisch **3 GB** (ONNX-Session 0,6–1,2 GB +
  BM25 + Graph 0,1–0,2 GB) — nicht blind auf 2 GB; erst nach Messung senken. Ingest **4–6 GB**.

---

## Konsolidierte Review-Befunde (Severity → Fix → Track)
| Sev | Befund | Track | Fix |
|---|---|---|---|
| KRIT | per-User-ACL Fail-safe-Inversion (leer=alles) | E | `access_all`-Codepfad, leer→`[]` |
| KRIT | REST/Web-UI ungated für Nicht-Admin | E | Rolle+ACL in `dependencies.py` |
| HOCH | `patch_document` ohne Qdrant-Sync (Split-Brain) | F/1 | gemeinsame Move-Funktion |
| HOCH | Löschen nur scope-gated (Bearer löscht) | E | `require_ui_admin` + delete-Scope raus |
| HOCH | Rollen-Enum `viewer` vs `user`, Default ADMIN | E | Constraint-Migration + Default `user` |
| HOCH | TOTP: Aktions-Bindung/Lockout/Bearer-Bypass | E | an Aktion binden, Lockout, `mcp_admin`+TOTP |
| HOCH | near_dup `folder_paths`-Union leakt Existenz | D | Schnittmenge mit Caller-ACL |
| HOCH | LSH (b,r)=8×16 verfehlt Jaccard-0.8 (~20 % Recall) | D | (b,r)=16×8 oder Schwelle ≥0,88 |
| HOCH | `nx.pagerank` braucht scipy (fehlt in Deps) | D | `scipy` + PPR-Warmup |
| HOCH | Latenz-Zahlen inkonsistent, 2–4 s unrealistisch für A0 | A | neu messen; Ziel an A1 |
| MITTEL | Track F Infra existiert nicht (Neubau) | F | folder_suggestions + Accept + Move bauen |
| MITTEL | Graph-Kohärenz api↔ingest ungeklärt | D | Boot-Load + Version-Refresh, nie pro Request |
| MITTEL | ACL-Subgraph pro Request teuer (~50–150 ms) | D | Cache pro ACL-Signatur |
| MITTEL | reindex aus chunks braucht JOIN documents | C | Payload aus Chunk⨝Document |
| MITTEL | Worker-Split: alle Ingest-Pfade enqueuen, Lifespan lösen | C | upload/rag_upload/watcher→queue |
| MITTEL | Sanitize zu eng (nur Labels) | D | alle LLM-Felder (tags/norm_id/citation…) |
| MITTEL | Content-Budget zählt list/get nicht | D | distinct doc_ids aus list/get zählen |
| MITTEL | RAM-Budgets nicht gesetzt | A/C | Serving 3 GB, Ingest 4–6 GB |
| MITTEL | `apply_suggestions` nicht admin-only/kein ACL | F | admin-only + per-Doc-ACL |
| NIEDR | `user_id` fehlt in QueryLog | E | Spalte + Keying |
| NIEDR | `chunk_id` muss meta-unabhängig sein | C | stabile Content-ID |
| NIEDR | Docling ~2–5 s/Seite (Bestands-Ingest lang) | C | Worker-Parallelität |
| NIEDR | kombinierter Autotag+Metadata-Prompt evtl. Qualität | C | nur nach A/B zusammenlegen |
| NIEDR | Single-Instance-Invariante (Limits/Budget in-memory) | E | dokumentieren o. Redis planen |

---

## Sicherheitsmodell (final)
| Rolle / Kanal | Scope | Ordner-Sicht | Löschen | Schutz |
|---|---|---|---|---|
| **User** (mehrere) via MCP/Web | `read` | `access_all ? alles : eigene` (Default: nichts) | nie | Login/OAuth + REST-Rollen-Gating |
| **MCP-Admin** via MCP | `read+write` | (alle) | nie über MCP | **TOTP pro Write** (aktionsgebunden, Lockout) |
| **Admin** via Web-UI | voll +delete | alle | **nur hier** (`require_ui_admin`) | Session |
Fünf Schichten: harte ACL (doppelt gegurtet, Schnittmengen-Sichtbarkeit) · read-only MCP ·
Löschen nur Web-UI/Admin · Struktur/Inhalt-Trennung + Content-Budget · Sanitize-all-LLM-Felder.
Bestehend bleibt: OAuth 2.1+PKCE, Rate-Limits, Audit (im Code als solide bestätigt).

---

## Reihenfolge & Gates
1. **Track C** — C0-Spike (GATE) → C1 Bau → C2/C2b Integration+Chunks → Worker-Split → WSL-Verify → Flag-Flip.
2. **Track D** — L1 → L2 (LSH-Fix, scipy, Boot-Load) → Fastpath/PPR/MCP-Tools (Sicherheits-Fixes) → messen → L3 (Phase 2, A/B).
3. **Track E** — parallel möglich (orthogonal): Migration + `access_all`-Codepfad + REST-Rollen-Gating + TOTP.
4. **Track F** — nach D (Communities): Infra-Neubau + gemeinsame Move-Funktion (heilt Split-Brain).
5. **Track A** — A0 (nach Neumessung) früh; A1 (ONNX/Quant) mit Worker-Split.

---

## WSL-Gesamttest (erweitert um Fix-Checks)
- **Ingest-E2E:** je Format (inkl. DOCX-Tabelle/pptx/Scan/kaputt) → `document_chunks` gefüllt,
  Qdrant-Chunkzahl=Child-Zahl, `report.json`, INDEXED; Determinismus (gleiche chunk_ids).
- **Move/Split-Brain:** `patch_document`-Move → Doc bleibt auffindbar (Qdrant `meta.folder`
  mitgezogen); Qdrant-Fehler → kein halber Move.
- **Multi-User-ACL:** User A(`/Steuer/`)/B(`/Bau/`) → je nur eigener Ordner in Retrieval
  **und** Graph; **neuer User sieht nichts** (Fail-safe); read-User kann über REST **nicht**
  löschen/exportieren (Rollen-Gating); Bearer-Key mit delete → abgelehnt.
- **TOTP:** MCP-Write nur mit gültigem, aktionsgebundenem Code; Replay/falsch → abgelehnt; Lockout greift.
- **Graph-Sicherheit:** ordnerübergreifende near_dup/Brücke leakt Fremd-Doc **nicht**; Sanitize aktiv.
- **Latenz:** Reranker sauber neu gemessen (Paar+Batch); A0 vs. A1 dokumentiert; Graph-Aufschlag gemessen (scipy an, Boot-Load, ACL-Cache).
- **Regression:** `verify_new.sh` 12/12 + `oauth_verify.sh` 27/27 grün.

## Offene Entscheidungen / Invarianten
- **Single-Instance:** Rate-Limits/Auth-Codes/Content-Budget sind in-memory → bei 2. rag-api-
  Replica schwächen sie sich still ab. Als Invariante halten oder Redis planen.
- **TOTP-Recovery** (verlorenes Gerät) — Prozess definieren.
- **L3** nur nach A/B-Beleg scharf schalten.
- **DSGVO-Löschung** = Admin-Web-UI (entschieden).

---
*Endgültiger Plan. Clean-Room (networkx/numpy/scipy BSD, Stdlib, qdrant-client Apache) —
keine übernommenen Codezeilen, keine Attributionspflicht. Nichts wird ohne ausdrückliche
Freigabe committet/gepusht; jeder Schritt live in WSL verifiziert.*
