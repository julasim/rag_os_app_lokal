# CLAUDE.md — Agent-Briefing für RAG OS

Diese Datei richtet sich an Claude (und an mich selbst in 6 Monaten). Sie steht
**nicht** im README, weil sie kein Marketing ist und keine Bedienungs-Anleitung.
Sie enthält das, was beim Code-Lesen *nicht* offensichtlich ist.

> **An Claude:** Bevor du die Arbeit in diesem Ordner beendest, aktualisiere
> diese Datei mit neuen Entscheidungen/Stand — besonders §13 (Sicherheit).
> **Zuletzt aktualisiert: 2026-07-20**

> ## ⚠️ STATUS 2026-07-20 — nativer Docker-freier Umbau KOMPLETT (M1–M8)
>
> Diese App ist **kein VPS-Docker-Stack mehr**. Der gesamte Umbau (Masterplan
> M1–M8) ist fertig, verifiziert und auf `main` (`c5ffd9b`). **Große Teile des
> Bodys unten (Qdrant/Postgres/Ollama/Haystack/OAuth/Docker/Edge-Proxy) sind
> HISTORISCH** — beim Lesen ignorieren bzw. gegen den echten Code prüfen.
> **Quelle der Wahrheit für die lokale Architektur: [BUILD-PLAN.md](BUILD-PLAN.md)**
> (Fortschrittstabelle + Meilensteine). Aktueller Stack in einem Satz:
>
> - **Ein Prozess, kein Docker.** FastAPI + MCP unter `uvicorn` @127.0.0.1, in einer
>   **pywebview/WebView2-Shell** (`app/desktop.py`, Tray/Autostart/Toast).
> - **LanceDB = EINZIGER Wissensspeicher** (`app/pipelines/store.py`, im Vault) —
>   ersetzt Qdrant **und** die Postgres-Korpus-Tabellen. Dazu ein kleines lokales
>   **`appstate.sqlite`** (Keys/Users/Log, NICHT im Vault).
> - **Embeddings: ONNX/fastembed `intfloat/multilingual-e5-large`** (1024-dim,
>   mehrsprachig; **nicht** bge-m3 — fastembed unterstützt es nicht — mit e5-Query/
>   Passage-Präfixen in `factory.py`). Reranker bge-reranker-v2-m3 als INT8-ONNX.
>   **Kein Ollama/LLM** — Tagging/Graph sind deterministisch (LLM-frei).
> - **Rollen (M8e):** `writer` (Ingest+Query, Docling/torch, schreibt Vault-Versionen)
>   vs. `reader` (query-only, liest lokalen Cache am LanceDB-`current`-Tag; kein
>   Docling/torch). Umschaltung über `settings().service_role`.
> - **MCP: Bearer-only, read-only** (kein OAuth/TOTP). Tools: `rag_overview`,
>   `rag_retrieve`, `norm_lookup`, `rag_list_documents`, `rag_get_document`(+Volltext),
>   `rag_stats`. UI hat lokalen **Auto-Login** (`local_ui_autologin`, 127.0.0.1).
> - **Publish/Versionierung** über LanceDB-Tags (`current`/`prev`) + Leser-Cache
>   (`app/pipelines/publish.py`). Backup = Vault-Kopie + appstate (`backup/engine.py`).
> - **Packaging (`build/`):** zwei Windows-Installer (Schreiber ~3 GB voll /
>   Leser ~2,5 GB) via PyInstaller + Inno-Setup; beide gebaut & Payload-Boot verifiziert.
> - **Gelöscht:** Docker/Compose/Caddy, `worker.py`, OAuth/TOTP, pg_dump/Qdrant-Backup,
>   Postgres-/Qdrant-/Ollama-/Haystack-Deps.

> **Änderungslog:**
> - 2026-07-20 — **M8 komplett + Installer gebaut (echter Windows-Build).** M8c
>   pywebview-Shell (`app/desktop.py`), M8d Packaging (`build/`: 2× PyInstaller-Spec
>   + 2× Inno-Setup + `build.ps1` + `fetch-models.py` + `make-icon.py`), M8e
>   Reader/Writer-Rollen-Split (lazy Ingest-Importe, Store-Cache-Rolle, `appsettings.py`,
>   Config-Aliase). **Beim echten Build 4 reale Bugs gefunden & gefixt:** (1) fastembed
>   kennt **bge-m3 nicht** → Umstieg auf **multilingual-e5-large** (1024-dim) +
>   e5-Präfixe; (2) **aiosqlite**/uvicorn-/SQLAlchemy-Dialekte dynamisch geladen →
>   `collect_all` in beide Specs (sonst crasht die eingefrorene App beim Import);
>   (3) torch-Lizenzbäume >260 Zeichen → `Remove-DistInfoLicenses` (robocopy) in
>   `build.ps1`; (4) Unicode-Prints → ASCII. **Verifiziert:** beide Installer
>   „Successful compile"; beide Payloads booten live (Health 200, sqlite+lancedb;
>   Writer startet Queue/Maintenance/Backup); `ruff` + `npm build` grün. Auf `main`
>   (`c5ffd9b`). Installer liegen lokal in `dist/` (gitignored).
> - 2026-07-19 — **M3-Rest → M7 + Tiefen-Audit** (LanceDB-Store komplett, LLM-freies
>   Tagging M5, In-Process-Queue M6, Publish/Versionierung M7; Audit reparierte
>   still-kaputte SQLite-Pipelines [array_agg/JSON-`.contains`] und entfernte tote
>   VPS-Infra). Auf `main` bis `8a6bc5a`.
> - 2026-07-16 — **Aufräumrunde vor Feature-Freeze** (3-Agenten-Audit: toter Code,
>   Bugs, Deps/Config/Doku). Auf `main` (`548b725` Cleanup, `2b0fcbe` Fixes,
>   `9b3a32a` OpenRouter, + Doku).
>   - **Toter Code raus:** `log_auth` (audit.py), `_doc_node_key` (store.py),
>     `detect_cross_project_duplicates`-Alias, No-Op `db/seed.py`; 5 tote Deps
>     (`aiofiles`/`alembic`/`markdown`/`pyyaml`/`email-validator`); Frontend-Reste
>     (`RetrieveResult`-Interface, Phantom-Felder `project`/`project_keep`/`project_remove`).
>   - **Bug-Fix (echt) — `api/suggest.py apply_suggestions`:** hatte **keine Ordner-ACL**
>     (IDOR: eingeschränkter Write-Key konnte fremde Docs verschieben) und einen
>     nicht-atomaren Eigenbau-Move (DocumentChunk blieb außen vor). Jetzt
>     `can_access_folder` auf Quell-+Zielordner + atomare `move_document()`. WSL-verifiziert.
>   - **Härtung — `api/maintenance.py accept_duplicate`:** nutzt jetzt die kanonische
>     `delete_qdrant_chunks` und **bricht bei Qdrant-Ausfall ab** (kein PG/Datei-Delete →
>     kein Split-Brain). **Korrektur zum Audit:** die Annahme „`store.delete_by_filter`
>     ist eine Phantom-Methode / stiller DSGVO-Bug" war **falsch** — die Methode existiert
>     real auf der Haystack-QdrantDocumentStore und löscht korrekt; es blieb nur das
>     schmale Ausfall-Fenster. Also Härtung, keine kritische Lücke.
>   - **GC-Absicherung** (watcher.py/main.py: create_task-Referenzen halten),
>     **defensiv** (query.py: uuid-Parse im geschützten `_log_query`-Block).
>   - **OpenRouter entfernt** (toter, unerreichbarer Cloud-LLM-Zweig — Betrieb ist
>     100 % lokal/Ollama).
>   - **Doku:** VERALTET-Header in `README.md` + `docs/ARCHITECTURE.md` (Projekt-Konzept/
>     Streamlit/`/api/query`/OpenRouter existieren nicht mehr — CLAUDE.md ist die Quelle).
> - 2026-07-16 — **M4 (Track F): Ordner-Reorg aus D-Communities — fertig.**
>   Neues Modul `graph/reorg.py` + Tabelle `folder_suggestions` (pending/accepted/
>   rejected, pro Doc eine Zeile, `current_folder` = Undo-Info). **Gruppierung
>   deterministisch** aus `graph_nodes.community_id`; nur Communities ab
>   `reorg_min_community_docs`, die über ≥2 Ordner **verstreut** sind. Ziel
>   deterministisch: dominanter Ordner (Anteil ≥ `reorg_dominant_folder_ratio`) →
>   der wird's (kein LLM, minimale Bewegung); sonst **LLM benennt NUR den Ordner**
>   (`_llm_folder_name`, qwen) mit robustem deterministischem Fallback (dominanter
>   Tag → norm-Präfix → Community-Label). Ersetzt nur pending; accepted/rejected
>   bleiben Historie, abgelehnte (doc,target) werden nicht erneut vorgeschlagen.
>   **Anwenden** über die atomare `move_document()` (M0.2 — Postgres+DocumentChunk+
>   Qdrant konsistent): `accept_suggestion` schreibt einen `MaintenanceLog`-Undo
>   (`folder_move`, 30 Tage), `undo_folder_move` verschiebt zurück + setzt die
>   Suggestion wieder pending; der bestehende `/log/{id}/undo` dispatcht jetzt nach
>   action_type. Endpunkte (alle `require_ui_admin`): `POST …/reorg/rebuild`,
>   `GET …/suggestions/folders`, `POST …/suggestions/folders/{id}/accept`
>   (+ **per-Doc-ACL** auf Quell- UND Zielordner via `can_access_folder`),
>   `.../reject`. Nachtlauf ruft `build_folder_suggestions` NACH dem Graph-Refresh.
>   Frontend: neue „Ordner-Vorschläge"-Karte auf der Wartungs-Seite (Diff
>   current→suggested, 1-Klick Übernehmen/Ablehnen/Neu-vorschlagen; Undo über das
>   Wartungs-Log). **WSL-verifiziert (Ubuntu-22.04, live):** verstreute Community →
>   LLM-Name `/hochbau-kosten/`, idempotent, Reject-Skip, Dominant-Pfad wählt
>   bestehenden Ordner; Accept verschiebt konsistent über PG+Chunk+Qdrant, Undo
>   stellt netto-null her; `npm run build` (tsc+vite) grün; HTTP-Ebene in-process
>   (ASGI) login 200 / unauth 401 / rebuild+list+accept+undo+reject alle 200.
>   Auf `main` (`b2ebc99` Modell+Generierung, `1eb451a` Accept/Reject/Undo,
>   `8cab927` UI). **Masterplan-Tracks C–F durch** (offen nur der bewusst
>   verschobene Scope: L3, Connectors, weitere Formate, Eval-Gold-Set,
>   at-rest-Verschlüsselung, Offsite-Autotrigger).
> - 2026-07-16 — **M3 (Track D) Increment (e)+(f): Retrieval-Integration +
>   Graph-Sicherheit — Track D fertig (außer L3, out of scope).** Neues Modul
>   `graph/store.py`: immutabler In-RAM-Snapshot (node_type/doc_folder/
>   `referencing_docs`-Reverse-Index/ungerichtete Adjazenz), lazy-load mit billigem
>   Versions-Token (Scalar-Subqueries node/edge-count + jüngste Zeitstempel) +
>   async-Lock + TTL (`graph_cache_ttl`) → **nie pro Request neu**; Ladefehler laut
>   geloggt → leerer Snapshot (Retrieval bleibt auf Hybrid). **Retrieval-Integration**
>   in `query.py run_retrieve`: **Fastpath** (`extract_refs` → norm/legal-Node →
>   referenzierende Docs, vorne eingereiht) + **PPR-Multi-Hop** (Seeds = Top-Hybrid-
>   Docs → verwandte Docs, angehängt, Reranker fusioniert). **SICHERHEITSKERN (M3f):**
>   `visible_doc_nodes(folder_paths)` = **Schnittmenge** der Document-Nodes mit der
>   aufgelösten Caller-ACL; `ppr_candidate_docs` läuft über den **ACL-restringierten
>   Subgraph** (Entity-Nodes ∪ nur sichtbare Docs) — ein unsichtbares Doc ist gar
>   nicht im Subgraph, PPR kann es weder liefern noch durchlaufen → near_dup/
>   similar_to-Sichtbarkeit ist die **Schnittmenge, nie die Vereinigung**.
>   `_sanitize_chunks` (jeder serialisierte Chunk MUSS ACL-sichtbar sein — letzte
>   Schranke) + `_apply_content_budget` (Deckel distinct doc_ids) + `_scrub_cross_refs`
>   (nullt `superseded_by`, wenn es aus der ACL hinauszeigt — das einzige
>   doc-übergreifende Feld). **Ehrliche Abweichungen vom Plan:** (1) „RRF-Fusion" →
>   bei aktivem Cross-Encoder-Reranker ist der die bessere Fusion; RRF wäre nur der
>   No-Rerank-Fallback. (2) Kein „ACL-Subgraph-Cache pro Signatur" — der Subgraph-Bau
>   ist bei Büro-Korpusgröße billig genug pro Request; Cache nachrüstbar, falls
>   Profiling es verlangt. (3) **L3-Entity-Layer bewusst weggelassen** (out of scope,
>   Masterplan). config: `graph_retrieval/fastpath/ppr_enabled`, PPR-Params,
>   `graph_content_budget`, `graph_cache_ttl`; `refresh_graph()` invalidiert den
>   Snapshot. **WSL-verifiziert (Ubuntu-22.04, live Postgres+Qdrant, 3 Docs ⨯ ÖNORM
>   B 1801-1 in /Test/,/C2b/,/DoclingTab/, near_dup /C2b/↔/DoclingTab/):** Fastpath
>   findet alle 3 referenzierenden Docs bei Vollzugriff, ACL-scoped auf 1 bei
>   Restriktion, leer ohne Verweis; PPR liefert verwandte sichtbare Docs; **LECK-TEST
>   bestanden** — /C2b/-beschränkter Caller erreicht via PPR NICHT das near_dup-Doc
>   in /DoclingTab/; voller run_retrieve /C2b//Test/-restringiert je nur eigener
>   Ordner, fail-safe-leer → nichts; `_scrub_cross_refs` nullt fremdes superseded_by
>   (outdated bleibt). Regression: ACL-Kern (`folders.py`/`dependencies.py`) + OAuth
>   unberührt → `oauth_verify` orthogonal; die von `verify_new` geschützte
>   Retrieval-ACL-Grenzklasse direkt gegen den Live-Stack mit diesem Code reproduziert
>   (der Audit-Harness in Ubuntu-24.04 ist ein Pre-Graph-Snapshot, nicht migriert).
>   ruff grün. Auf `main` (`bcdd000` e-1, `287d7f7` e-2/f). **Nächster Schritt: M4
>   (Track F — Ordner-Reorg).**
> - 2026-07-16 — **M3 (Track D) Increment (d): Nachtlauf-Analyse.** Neues Modul
>   `graph/analyze.py` `analyze_graph()`: **Louvain**-Communities (networkx,
>   fixer Seed) + **PageRank** (eigene numpy-Power-Iteration, gerichtet →
>   God-Nodes; **bewusst kein scipy**, hält das Slim-Image schlank) +
>   **Participation-Coefficient** (ungerichtet). Schreibt `community_id`/`pagerank`/
>   `participation` zurück in `graph_nodes` (Core-Table-Update per bindparam-
>   executemany — ORM-Entity-Update triggert den „bulk-by-PK"-Pfad) und füllt
>   `graph_communities` (Conductance nur bei echter Teilmenge, Fingerprint = sha1
>   der sortierten Mitglieder, provisorisches Label = canonical_key des PageRank-
>   stärksten Mitglieds, **kein LLM** — Naming ist Track F). Orchestrator
>   `graph/refresh.py` `refresh_graph()` = L1→L2→Analyse; `POST /api/graph/rebuild`
>   und der **Nachtlauf** (`maintenance/engine.py run_maintenance`, Fehler laut
>   geloggt, kippt die übrige Wartung nicht) rufen ihn. Dep `networkx>=3.2` explizit
>   in pyproject (war transitiv schon da). **WSL-verifiziert (live):** God-Nodes
>   (referenzierte Norm PageRank > Leaf-Doc), Bau/Recht-Cluster getrennt (4
>   Communities), alle Nodes zugeordnet, Community-Tabelle befüllt, **2 Läufe
>   identisch (deterministisch)**. 6/6 grün. Offen: (e) Retrieval-Integration
>   (Fastpath + PPR), (f) Graph-ACL (sicherheitskritisch). Auf `main`.
> - 2026-07-16 — **M3 (Track D) Increment (c): L2-Ähnlichkeitsschicht.** Neues
>   Modul `graph/l2.py` `build_l2()` mit zwei symmetrischen Doc-Relationen
>   (`layer='L2'`, ungeordnete Kante `src<tgt`): **similar_to** (Cosine der
>   Doc-Zentroide aus den dichten bge-m3-Chunk-Vektoren via Qdrant-Scroll,
>   sparsifiziert über **mutual-kNN** + τ) und **near_dup** (eigene **MinHash**,
>   128 Perm., deterministisch/fixer Seed, Kandidaten via **LSH (b,r)=16×8**,
>   geschätzte Jaccard ≥ τ). Schwellen über `settings()`
>   (`graph_sim_threshold=0.60`, `graph_sim_top_k=8`, `graph_neardup_threshold=0.85`,
>   `graph_shingle_size=5`). Voller L2-Rebuild (löscht nur `layer='L2'`, L1 unberührt).
>   `POST /api/graph/rebuild` läuft jetzt **L1 dann L2** (Nodes vor Ähnlichkeitskanten).
>   `graph*` in `pyproject.toml packages.find` ergänzt. **Bewusste Abweichung:** die
>   Zentroide werden bei der kleinen Korpusgröße **exakt** in numpy verglichen (besser
>   & deterministisch statt Qdrant-ANN; bei Wachstum umstellbar). **WSL-verifiziert
>   (live Qdrant):** similar_to findet echte Paare (0.80–1.0), near_dup trifft eine
>   angehängte Fast-Kopie (MinHash-Schätzung 0.92 ≈ echte Jaccard) und **nicht**
>   verschiedene Docs; die Zwei-Schichten-Trennung greift (0.80-ähnliche Docs sind
>   similar_to, **nicht** near_dup); 2. Lauf identisch (idempotent). 5/5 Checks grün.
>   Offen: (d) Analyse (Louvain/PageRank, Deps networkx/scipy), (e) Retrieval,
>   (f) Graph-ACL. Auf `main`.
> - 2026-07-16 — **M3 (Track D) Increment (a)+(b): Graph-Modelle + L1.** Neue
>   Tabellen `graph_nodes`/`graph_edges`/`graph_communities` (`db/models.py`,
>   via `create_all` idempotent angelegt; nur der GIN-Index auf
>   `graph_nodes.folder_paths` explizit in `session.py` — für die spätere
>   ACL-Containment-Suche). Node-Identität = String-PK `"{node_type}:{canonical_key}"`.
>   **L1 deterministisch:** `ingest/graph_refs.py` (Regex ÖNORM/EN/ISO/DIN + §/Art.,
>   normalisiert **ausschließlich** über `graph/canonical.py`) → `graph/build.py`
>   `build_l1()` baut die Kanten `references`/`supersedes`/`issued_by`/`has_tag`/
>   `in_folder` aus `document_chunks ⨝ documents`. Voller L1-Rebuild (löscht nur
>   `layer='L1'`), Nodes **geupsertet** ohne `pagerank`/`community_id`/`participation`
>   zu berühren (verwaltet die Nachtlauf-Analyse, Increment d). Admin-Endpoint
>   `POST /api/graph/rebuild` (`require_ui_admin`, Muster wie `/api/quantize`).
>   **WSL-verifiziert (Ubuntu-22.04, live Postgres):** Migration legt Tabellen +
>   GIN an; L1 extrahiert Normverweise korrekt (ö erhalten), alle Ausgaben einer
>   Norm kollabieren zu **einem** God-Node über mehrere Ordner, supersedes zwischen
>   verschiedenen Normnummern greift, Selbst-supersedes (gleiche Norm, andere
>   Ausgabe) wird übersprungen, 2. Lauf identisch (idempotent). 9/9 Checks grün.
>   Offen: (c) L2 (kNN/MinHash), (d) Analyse, (e) Retrieval-Integration,
>   (f) Graph-ACL (sicherheitskritisch). Auf `main`.
> - 2026-07-16 — **M2 (Track A0): billiges Speed (kein Re-Index).** `_log_query`
>   **fire-and-forget** (raus aus dem Retrieval-Hot-Path, `pipelines/query.py`);
>   **API-Key-Prefix-Index** — `ApiKey.key_prefix` (erste 16 Zeichen) + Index + Migration,
>   `verify_api_key` filtert Kandidaten per Prefix statt ALLE per bcrypt (Bestands-Keys mit
>   NULL-Prefix bleiben Fallback); api-CPU-Limit 2→4. **Ehrliche Korrektur zum Plan:**
>   `retrieve_k` bleibt `top_k*3` — der ONNX-Reranker ist jetzt billig, weniger Kandidaten
>   würde nur die Recall senken (onnxruntime-Thread-Tuning ohne Benchmark übersprungen).
>   WSL-verifiziert: Prefix neu/legacy/falsch, Retrieval + fire-and-forget-Log geschrieben.
>   Auf `main`.
> - 2026-07-16 — **M1.5: Docling = Standard-Ingest-Deploy (dokumentiert, §10).** Der
>   Docling-Zwei-Container-Modus (`-f docling`) ist ab jetzt der reguläre Prod-Deploy
>   (behebt den DOCX-Tabellen-Verlust). config-Default `ingest_backend` bleibt `legacy`
>   als Slim-only-Fallback; Rollback-Ventil `INGEST_BACKEND=legacy` (kein Rebuild).
>   Bestandsdaten bleiben (kein Auto-Reindex), tabellenlastige einzeln via Docling neu
>   einlesen. Docling-E2E bereits in C3b-W3 verifiziert (Tabelle erhalten). Auf `main`.
> - 2026-07-16 — **C3b-W3: Slim-api/Fett-Worker-Split verdrahtet.** Im Docling-Modus
>   erbt `api` jetzt das **slim** `sima-rag-api:latest` (torch-frei), `rag-ingest` baut das
>   **fette** `rag-ingest:latest` (eigener `build:` in `docker-compose.docling.yml`, vorher
>   baute die api das fette Image). **Zweistufiger Build zwingend** (rag-ingest ist
>   `FROM sima-rag-api:latest`): `docker compose build api` → `docker compose -f … -f docling
>   build rag-ingest` → `up -d`. WSL-verifiziert: api `import torch`→ImportError +
>   `ingest.worker.delegated`; rag-ingest `queue.worker_started`; Tabellen-DOCX über die Queue
>   → docling → **indexed (~56s), Tabelle erhalten**. Auf `main`.
> - 2026-07-16 — **M1.4: Qdrant Scalar-INT8-Quant (kein Re-Embed).**
>   `pipelines/factory.py enable_quantization(enable)` + Admin-Endpoint
>   `POST /api/quantize?enable=…`. Collection nutzt **benannte** Vektoren
>   (`text-dense`+`text-sparse`) → Quant muss **pro Named-Vector** über
>   `vectors_config={"text-dense": VectorParamsDiff(quantization_config=…)}` gesetzt
>   werden (collection-weit greift's NICHT). `ScalarQuantization(INT8, quantile=0.99,
>   always_ram=True)`; Genauigkeit über Qdrants Default-Rescore (Originale `on_disk`).
>   Voll reversibel (`Disabled`). WSL-verifiziert: enable→`text-dense`-Quant an,
>   Retrieval liefert weiter Chunks, disable→zurückgesetzt. Auf `main`.
> - 2026-07-15 — **M1.3: Serving-Image torch-frei (ONNX-Reranker) + Ingest-Image
>   air-gapped.** Reranker läuft jetzt als **INT8-ONNX** (`onnxruntime`+`transformers`-
>   Tokenizer, `app/pipelines/reranker.py`, Modell in Wegwerf-Build-Stage exportiert →
>   `/opt/models/reranker`, `local_files_only`) statt sentence-transformers/torch →
>   **Serving-Image 11,4 GB→3,13 GB** (vorher ~3,6 GB *mit* torch), api-RAM-Limit 4g→3g.
>   `Dockerfile.ingest` (=`FROM` slim) trägt torch/torchvision/docling + **Offline-Bake**
>   (docling-Modelle, bge-m3-Tokenizer, **BM25** `Qdrant/bm25`) nach `/opt/models`;
>   `docling_ingest.py offline=True`. WSL-verifiziert: Reranker rankt relevante DE-Passage
>   #1; `--network none` Docling-Parse **verlustfreie DOCX-Tabelle** + BM25 offline.
>   Reviewer-Fixes: **W1** BM25 mitgebacken (sonst scheitert jeder Offline-Docling-Job),
>   **W2** stiller Tokenizer-Fallback in `doc_ingest/chunk.py` laut geloggt. **W3
>   offen (C3b):** im Docling-Modus baut `api` noch aus `Dockerfile.ingest` (fett) — Slim-
>   api/fett-Worker-Split ist der nächste Schritt (Zwei-Image-Build-Reihenfolge). Auf `main`.
> - 2026-07-16 — **M1.3-Hotfix: air-gapped Offline-Env robust.** Die Offline-Flags
>   (`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`) waren NICHT im Env (die frühere Notiz „im
>   Compose-Env" stimmte nicht) → `IngestConfig.offline` setzte sie erst zur `convert()`-Zeit,
>   also NACH dem transformers-Import, wo huggingface_hub das Flag schon gecacht hat → der
>   erste Docling-Job versuchte einen HF-Zugriff, fiel bei `--network none` auf den
>   ungebackenen docling-Default `all-MiniLM` zurück und crashte. Fix: beide Flags als
>   **ENV im `Dockerfile.ingest`** (NACH dem Bake gesetzt, sonst scheitern die Bake-Downloads)
>   → intrinsisch im Image, importzeitsicher, nicht in Compose vergessbar. WSL-verifiziert:
>   `docker run --network none` OHNE externes Offline-Flag → BM25 offline + Docling-Parse mit
>   **verlustfreier Tabelle** (`table_html`). Auf `main`.
> - 2026-07-15 — **Großer Umbau begonnen — aktiver Plan:
>   [docs/RAG-OS-MASTERPLAN.md](docs/RAG-OS-MASTERPLAN.md)** (Tracks C–F, mit
>   3-Agenten-Review). Neu & in isolierter WSL-Umgebung **end-to-end verifiziert**
>   (Details §16): **layout-aware Parsing** via Docling (`app/doc_ingest/` + Adapter
>   `app/ingest/docling_ingest.py`, Feature-Flag `ingest_backend` in config, **default
>   `legacy`** → Rollback) — behebt den stillen **DOCX-Tabellen-Verlust** des
>   Legacy-Parsers (`parsers.py` liest `word.tables` nie); **kanonische Chunk-Schicht**
>   `DocumentChunk` (Postgres = Wahrheit, Qdrant abgeleitet); **Docling-Ingest-Image**
>   `app/Dockerfile.ingest` + `docker-compose.docling.yml`; **Graph-Fundament**
>   `app/graph/canonical.py` (ID-/Normnummern-Normalisierung, Track D). OAuth/ACL/
>   Retrieval **unverändert**. Nächste Schritte: §16.
> - 2026-07-13 — **OAuth für MCP sauber neu gefasst** (Claude.ai-Connector),
>   default AN: Identität = echte UiUser, Config via `settings()`, Storage in
>   Postgres, korrekte `iss`/`aud`, gehärtet (Rate-Limits/Cap/Cleanup). Live in
>   WSL 27/27 (Roundtrip + Angriffsmatrix + Persistenz). Details §15.
> - 2026-07-13 — Ausbau zum Büro-Brain (Retrieval-Qualität), live in WSL
>   verifiziert (11/12 + section_path separat bewiesen, Security-Regression
>   12/12): **echtes Hybrid-Retrieval** (dense Ollama + BM25-sparse via
>   fastembed, `QdrantHybridRetriever`, RRF) — exakte Normnummern/§/Codes;
>   **Reranker default AN** (Achtung RAM, siehe unten); **strukturierte
>   Metadaten** (doc_type/norm_id/version/…) via LLM-Extraktion + Filter;
>   **Versions-/Ablöse-Logik** (`superseded`); **hierarchische section_path**
>   + `citation`-Feld. Details §14.
> - 2026-07-11 — Zweites Review (statisch) + Sanierung/Härtung vor Multi-User:
>   (a) **MCP-only** — REST-Suche (`/api/retrieve` + `/api/query`), `rag_search`
>   und der lokale-LLM-**Antwort**pfad (`run_query`) entfernt; Suche läuft nur
>   noch über MCP `rag_retrieve`. (b) Kanonische Ordner-ACL
>   ([app/auth/folders.py](app/auth/folders.py)) — löst mehrere Zugriffs-Lücken
>   an der Wurzel (siehe §13). (c) Toter Code weg: `app/ui/` (Streamlit),
>   `api/projects.py`, Frontend-Projects, `config/*.yml`, Migrationsskript,
>   `streamlit`/`pandas`-Deps, vestigialer `project`-Param. Details §13.
> - 2026-07-07 — Vollständiges Pre-Prod-Sicherheits-/Produktionsaudit (statisch +
>   dynamische Live-Tests). Alle kritischen/hohen + wichtige mittlere Befunde
>   behoben und verifiziert; Off-Site-Backup auf externe Platte gebaut. Details
>   in §13.
> - (davor) — Init/Korrektur des Briefings nach Entfernung des „Projekt"-Konzepts
>   (nur noch Ordner + Tags), React-Frontend, Edge/Standalone-Deployment-Split.

Für die volle Vision siehe [docs/VISION.md](docs/VISION.md).
Für die aktuelle Roadmap siehe das jeweils aktuelle Plan-File unter
`~/.claude/plans/`.

---

## 1. Was das System ist (Kurzform)

Selbstgehosteter Such-Knoten (Retrieval-as-a-Service) über das Wissen von Julius.
Antwort-Generierung passiert beim Client (Claude/GPT/Langdock), nicht im System.
Details: [docs/VISION.md](docs/VISION.md).

## 2. Goldener Pfad zum Verstehen des Codes

Beim Einstieg in dieser Reihenfolge lesen — dann ist das Bild komplett:

1. [app/config.py](app/config.py) — wie Settings geladen werden
2. [app/main.py](app/main.py) — Lifespan, Router-Mounts, MCP-Mount, ASGI-Dispatch
3. [app/pipelines/query.py](app/pipelines/query.py) — Retrieval-Pfad (`run_retrieve`)
4. [app/ingest/pipeline.py](app/ingest/pipeline.py) — Datei → Qdrant (Backend-Weiche
   `ingest_backend`: `legacy` = PyMuPDF/python-docx + struktureller Chunker, `docling`
   = layout-aware; schreibt Chunks kanonisch nach Postgres `DocumentChunk`, dann Qdrant)
5. [app/doc_ingest/](app/doc_ingest/) — layout-aware Parsing/Chunking (Docling),
   `ingest(path) → IngestResult`; [app/ingest/docling_ingest.py](app/ingest/docling_ingest.py) = Adapter (nur bei `ingest_backend=docling`)
6. [app/graph/canonical.py](app/graph/canonical.py) — kanonische ID-/Normnummern-
   Normalisierung (Track-D-Fundament; von L1/L3/Query-Fastpath identisch zu nutzen)
7. [app/mcp_server/server.py](app/mcp_server/server.py) — MCP-Tool-Definitionen
8. [app/auth/dependencies.py](app/auth/dependencies.py) — Scopes + `can_access_folder`
9. [app/auth/folders.py](app/auth/folders.py) — **kanonische** Ordner-ACL (einzige Quelle)

## 3. Zwei harte Konventionen

- **Config nur über `settings()`** ([app/config.py](app/config.py)) —
  niemals `os.environ` direkt. Auch nicht in Tests. (Es gibt kein
  `projects_config()` mehr — Projekt-Konfig wurde ersatzlos entfernt, siehe §4.)
- **Strukturiertes Logging** ([app/logger.py](app/logger.py)) im Format
  `log.info("event.name", key=value)`. Keine F-String-Logs, kein `print`.
- **Blockierende Haystack-Calls immer in `asyncio.to_thread`** — siehe
  [app/pipelines/query.py](app/pipelines/query.py) und
  [app/ingest/pipeline.py](app/ingest/pipeline.py). Sonst blockiert der
  Eventloop während Embedding/LLM-Inferenz.

## 4. Mental Model für Daten

**Kein "Projekt"-Konzept mehr.** Bis Anfang Juli 2026 gab es eine dritte
Dimension "Projekt" (= eigene Qdrant-Collection, eigene API-Key-Whitelist).
Sie wurde vollständig entfernt und durch reine Ordner-Hierarchie ersetzt
(siehe Commits `578415b`, `4b94bd6`, `e356c59` — "Aura Explorer-Redesign").
Falls ein alter Vorschlag/Plan noch von `project`, `allowed_projects` oder
`projects_config()` spricht: das ist Alt-Wissen, nicht nachbauen.

Aktuell zwei orthogonale Dimensionen:

- **`folder_path`** = freier Text-Pfad (organisch, entsteht beim Upload,
  beliebig nestbar, VS-Code-artiger Explorer im Frontend). Filter in REST:
  `folder=/Steuer/`. Filter in Qdrant-Payload: `meta.folder_path` (mit
  `meta.`-Prefix, weil Haystack die Doc-Metadaten unter `meta` ablegt).
  Zugriffskontrolle läuft jetzt hierüber: `ApiKey.allowed_folders` +
  die **kanonische** ACL in [app/auth/folders.py](app/auth/folders.py)
  (`is_within` / `key_allows_folder` / `accessible_folder_paths`).
  `AuthContext.can_access_folder()` ist nur noch ein dünner Wrapper darum —
  siehe §13.
- **Tags** = TEXT[] (cross-cutting). Manuell vom User oder vom LLM beim Ingest
  vorgeschlagen ([app/ingest/autotag.py](app/ingest/autotag.py)).

Es gibt genau **eine** globale Qdrant-Collection (`rag_documents`, siehe
[app/pipelines/factory.py](app/pipelines/factory.py)) für alle Dokumente —
keine Collection pro irgendwas mehr.

Postgres ([app/db/models.py](app/db/models.py)) ist Single-Source-of-Truth für
"was haben wir?", Qdrant für "wo steht es?". Wenn die zwei auseinanderdriften
(z.B. Doc in Postgres, kein Chunk in Qdrant) → Bug, nicht Feature.

**Altlasten (2026-07-11 gelöscht):** `config/projects.yml`,
`config/project_defaults.yml` und `scripts/migrate-projects-to-db.py` aus der
Projekt-Ära sind entfernt, ebenso der vestigiale `project`-Form-Parameter an den
Upload-Endpunkten. `docs/ARCHITECTURE.md` und `README.md` beschreiben
stellenweise noch das alte Drei-Ebenen-Modell mit „Projekt" — beim Lesen gegen
den tatsächlichen Code prüfen, nicht blind übernehmen.

## 5. API-Oberfläche: Suche nur über MCP

**Seit 2026-07-11 ist Suche MCP-only.** Die REST-Endpunkte `POST /api/retrieve`
und `POST /api/query`, das MCP-Tool `rag_search` und der lokale-LLM-**Antwort**pfad
(`run_query`) sind entfernt. Es gibt genau **einen** Such-Pfad:

- **`rag_retrieve`** (MCP) — liefert nur Chunks + Quellen, keine LLM-Antwort.
  Der konsumierende Client (Claude/GPT/Langdock) formuliert selbst.
  Prüft `read`-Scope; die Ordner-ACL wird **serverseitig** in
  [app/pipelines/query.py](app/pipelines/query.py) (`run_retrieve` →
  `accessible_folder_paths`) erzwungen — nicht dem Client-`folder`-Parameter
  vertrauen.

Die REST-API (`/api/*`) deckt nur noch **Verwaltung** ab: Dokumente, Keys,
System, Wartung, Suggest. Das Ollama-LLM bleibt für **Embeddings**
(`bge-m3`) und für Auto-Tagging/Ordnervorschläge
([app/ingest/autotag.py](app/ingest/autotag.py),
[app/services/folder_suggester.py](app/services/folder_suggester.py)) in Betrieb
— nur die *Antwort-Generierung* zur Suche ist weg.

Vollständige Schema-Referenz in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
(Achtung: dort ist der Projekt- und der `/api/query`-Bezug veraltet).

## 6. Dev-Workflow auf Windows

Der Code lebt auf Windows (OneDrive-Synced), läuft aber im Linux-Container.
Volume-Mount in [docker-compose.yml](docker-compose.yml) macht Code-Edits
sofort wirksam:

- Python-Edit → `docker compose restart api` reicht (uvicorn-Reload greift bei meisten Änderungen sogar ohne Restart); `make restart` für einen kompletten Stack-Neustart
- Dockerfile- oder pyproject-Edit → `make rebuild`
- Postgres-Schema-Edit ([app/db/models.py](app/db/models.py)) → Tabelle manuell droppen oder neue Migration; `init_db()` legt nur fehlende Tabellen an
- Frontend-Edit ([app/frontend/src](app/frontend/src)) → siehe §12, entweder Vite-Dev-Server oder `make rebuild`

**Kein automatisiertes Test-Setup vorhanden** (kein `tests/`-Ordner, kein
pytest in [app/pyproject.toml](app/pyproject.toml)). Verifikation läuft über
manuelles Durchklicken/curl gegen den laufenden Container plus
`ruff check app` ([app/pyproject.toml](app/pyproject.toml) `[tool.ruff]`) für
Lint. Wenn Tests entstehen: gegen echtes lokales Postgres/Qdrant, siehe Anti-Goals.

## 7. Anti-Goals

Was nicht zu tun ist, auch wenn es naheliegt:

- **Keine neue LLM-Antwort-Generierung im Tool ausbauen.** Das System ist
  Retrieve-only. Wer Antworten will, formuliert sie im konsumierenden Client.
- **Keine neuen Abstraktionen ohne konkreten zweiten Use-Case.** Drei ähnliche
  Code-Stellen schlagen eine vorzeitige Abstraktion.
- **Keine Mocks für DB oder Qdrant in Tests.** Ein Lokal-Postgres und ein
  Lokal-Qdrant sind über Docker greifbar — Mock-Tests, die nicht den echten
  Pfad treffen, geben falsche Sicherheit.
- **Keine `--no-verify`-Commits.** Wenn Hooks fehlschlagen: Ursache fixen,
  nicht überspringen.
- **Kein `os.environ` umgehen** der `settings()`-Schicht.
- **Kein Streamlit-State (`st.session_state`) für Auth** — Auth läuft über
  Cookie/JWT im API-Backend ([app/auth/](app/auth/)).

## 8. Selbst-Pflege-Spielregeln (Maintenance-Engine)

Wenn die Maintenance-Engine läuft, gilt:

- **Niedrigrisiko = autonom.** Tag-Synonyme (Edit-Distance ≤ 2 *und*
  Embedding-Cosine ≥ 0.9) werden automatisch zusammengeführt. Jede Aktion
  landet in `maintenance_log` mit Undo-Payload. 30 Tage Undo-Fenster.
- **Hochrisiko = bestätigungspflichtig.** Ordner-Verschiebungen und
  Duplikat-Löschungen landen als `pending` in `folder_suggestions` /
  `duplicate_suggestions`. Mensch akzeptiert mit 1 Klick in der UI.
- **Keine stillen Änderungen.** Jeder Maintenance-Lauf produziert
  Log-Events (`maintenance.*`) und einen UI-sichtbaren Stand.

## 9. Wo wir gerade stehen

Kein fixer Phasen-Stand hier eintragen — das rottet sofort. Stattdessen für
den aktuellen Stand: `git log --oneline -20` (Commit-Präfixe wie "Welle N"
oder "Aura" markieren größere Wellen) und die aktiven Pläne unter
`~/.claude/plans/`. Vision in [docs/VISION.md](docs/VISION.md).

Wenn ein Vorschlag von einem dieser Pfade abweicht: **erst die Plan-Datei
oder die Vision aktualisieren, dann den Code anfassen.** Nicht umgekehrt.

## 10. Deployment-Modi (Edge vs. Standalone)

Seit Mai 2026 zwei Compose-Varianten:

- **Edge-Mode (Default)** — `docker compose up -d` aktiviert nur die 4 Backend-Services (api, qdrant, ollama, postgres). `rag-api` hängt am externen Docker-Netz `proxy` und wird vom zentralen Edge-Caddy in `/opt/Proxy/` auf der VPS exponiert. **Kein eigener Caddy im Stack.**
- **Standalone-Mode** — `docker compose -f docker-compose.yml -f docker-compose.standalone.yml up -d` bringt einen eigenen `rag-caddy` als 5. Service zurück (mit Ports 80/443). Nur sinnvoll wenn RAG_OS alleine auf einer Box läuft.

Override-Pattern (Standalone): `docker-compose.standalone.yml` definiert NUR die zusätzlichen Services/Netze (rag-caddy, caddy-data/config volumes). Wird über die Default-Compose drüber-gemerged. Saubere Trennung, kein Code-Branching.

`docker-compose.localonly.yml` ist eine dritte, separate Variante für lokales Dev — nicht mit `.standalone.yml` verwechseln.

### Ingest-Topologie: Docling = Standard (M1.5, 2026-07-16)

Orthogonal zu Edge/Standalone (das ist nur Networking): der **Docling-Zwei-Container-
Modus ist der Standard-Ingest-Deploy** — er behebt den stillen DOCX-Tabellen-Verlust
des Legacy-Parsers (SIMA arbeitet tabellenlastig).

- **Standard (empfohlen):** `-f docker-compose.yml -f docker-compose.docling.yml`.
  `api` = slim `sima-rag-api:latest` (torch-frei, ONNX-Reranker, nur HTTP+Retrieval);
  `rag-ingest` = fettes `rag-ingest:latest` (torch/docling, air-gapped gebackene Modelle,
  `INGEST_BACKEND=docling`, `SERVICE_ROLE=ingest`). **Zweistufiger Build** (rag-ingest ist
  `FROM sima-rag-api:latest`): `docker compose build api` → `docker compose -f … -f docling
  build rag-ingest` → `up -d`.
- **Slim-only-Fallback:** ohne den docling-Override läuft nur die Ein-Container-api
  (`SERVICE_ROLE=all`, torch-frei) mit dem **legacy**-Parser (config-Default
  `ingest_backend=legacy`) — kein Docling, keine Tabellen-Extraktion. Nur wenn RAM/Betrieb
  es verlangen.
- **Rollback-Ventil:** `INGEST_BACKEND=legacy` (Env am rag-ingest) schaltet den Worker ohne
  Rebuild auf den Legacy-Parser zurück.
- **Bestandsdaten:** vor M1.5 mit legacy geparste Docs **bleiben** (kein Auto-Reindex);
  tabellenlastige (Leistungsverzeichnisse/ÖNORM) bei Bedarf **einzeln via Docling neu einlesen**.

## 11. Edge-Proxy / Public-Reachability (KRITISCH bei VPS-Deploy)

Der zentrale Edge-Caddy (Repo [`julasim/Proxy`](https://github.com/julasim/Proxy)) deployed unter `/opt/Proxy/` terminiert TLS für ALLE Stacks auf der VPS. Er besitzt VPS-Ports 80+443 exklusiv. RAG_OS wird unter `rag-os.sima.business` exponiert. Caddyfile-Block (zur Referenz):

```caddyfile
rag-os.sima.business {
    import html_security_headers
    handle /api/* { reverse_proxy rag-api:8000 ... }
    handle /mcp/* { reverse_proxy rag-api:8000 ... }
    handle       { reverse_proxy rag-api:8000 ... }   # FastAPI liefert auch das React-Frontend + WebSocket
    import access_log
}
```

**Goldene Regeln (Verstoß bricht andere Stacks auf derselben Box):**
- Default-Compose darf NIE `ports: "80:80"` deklarieren — gehört in `docker-compose.standalone.yml`
- Container-Name `rag-api` nicht ändern — edge-caddy referenziert per Name
- `rag-api` muss am externen `proxy`-Netz hängen (siehe `docker-compose.yml`)
- Wenn jemand das Repo neu cloned und ohne edge-caddy starten will: EXPLIZIT `-f docker-compose.standalone.yml` dazu
- **Nach `--force-recreate`/`make restart` von `rag-api` → `502`**: der Container hat eine neue IP, der Edge-Caddy cacht die alte. Fix: `docker exec edge-caddy caddy reload --config /etc/caddy/Caddyfile` (die App ist intern gesund, nur der Proxy zeigt ins Leere). Deshalb `rag-api` nur bei Bedarf recreaten.
- **`RAG_DOMAIN` in `.env`** muss exakt die DNS-/Caddy-Domain sein — Prod: `rag-os.sima.business` (ohne `.at`). Falsch → `make health` „Could not resolve host" + CORS auf falscher Origin.

Historischer Kontext: am 2026-05-11 hat ein RAG_OS-Standalone-Default die VPS-80/443 weggeschnappt — KI_WIKI-MCP/Dashboard + Bau-OS waren extern weg. Daraufhin der Edge-/Standalone-Split. Wenn der Fehler nochmal aufpoppt (irgendein Stack hat Default-Caddy → Konflikt): `docker stop <fremd-caddy> && docker rm <fremd-caddy> && cd /opt/Proxy && docker compose up -d`. Multi-Stack-Doku in `Proxy/CLAUDE.md`.

## 12. Frontend (React)

Seit Mai 2026 läuft die Admin-UI ("Aura Explorer", VS-Code-artiger
Ordnerbaum) als React/Vite/TypeScript-App. Streamlit ist komplett weg — der
tote `app/ui/`-Ordner und die `streamlit`/`pandas`-Dependencies wurden
2026-07-11 entfernt. Die UI ist reine Admin-Oberfläche (Dashboard, Dokumente,
Keys, System, Wartung); **keine** Suchseite mehr (Suche läuft über MCP, §5).

- Source: [app/frontend/src](app/frontend/src) (**nicht** `frontend/` im Repo-Root)
- Built: `app/ui_static/` (vom Dockerfile kopiert, gitignored) — von
  [app/main.py](app/main.py) als Static Files + SPA-Fallback auf `/` serviert
- Dev-Server: `cd app/frontend && npm run dev` (Vite, proxied `/api/*` auf
  `localhost:8000`); Build: `npm run build` (`tsc && vite build`)

Build passiert im Docker-Multi-Stage automatisch. Nach Frontend-Änderungen
für den Container: `make rebuild` oder `docker compose build api`.

API-Endpunkte bleiben auf `/api/*` und `/mcp/*` — docker-compose.yml exponiert
nur noch Port 8000 (kein 8501 mehr).

## 13. Sicherheits-Audit Juli 2026 — behobene Befunde & Absicherung

Vor der geplanten **Mehrbenutzer-Einführung bei SIMA Architecture** wurde ein
vollständiges Audit (statisch + dynamisch in isolierter WSL2/Docker-Umgebung,
mit Live-Exploitation) gefahren. Die folgenden Fixes sind eingespielt und live
verifiziert — **nicht rückbauen, das waren echte, reproduzierte Lücken:**

### Nachtrag 2026-07-11 (zweites, statisches Review — noch NICHT live-verifiziert)

Vollständige Befunde: [docs/SICHERHEITSKONZEPT.md](docs/SICHERHEITSKONZEPT.md) +
[docs/SANIERUNGSKONZEPT.md](docs/SANIERUNGSKONZEPT.md). Fixes:

- **Kanonische Ordner-ACL** ([app/auth/folders.py](app/auth/folders.py)) —
  `is_within` / `key_allows_folder` / `accessible_folder_paths`. Vorher gab es
  drei divergierende Checks (u.a. nacktes `startswith` im MCP-Server →
  `/Steuer` erlaubte `/Steuerberatung-Fremd/`). REST **und** MCP nutzen jetzt
  ausschließlich diese Funktionen. **Neue Endpunkte NIE mit eigenem
  `startswith` — immer über folders.py.**
- **Retrieval-ACL serverseitig erzwungen** ([app/pipelines/query.py](app/pipelines/query.py)
  `run_retrieve`): ein Key mit eingeschränkten `allowed_folders` durchsuchte
  ohne `folder`-Parameter die **gesamte** Collection. Jetzt löst
  `accessible_folder_paths` die erlaubten Ordner (inkl. Unterordner) aus
  Postgres auf → Qdrant-`in`-Filter auf `meta.folder`. Leere Auflösung =
  **leere** Antwort, nie ungefiltert.
- **Export-Endpunkt abgesichert** ([app/api/documents.py](app/api/documents.py)
  `export_documents`): hatte KEINE Ordnerprüfung → IDOR (fremde Docs per ID
  exportierbar). Jetzt `read`-Scope + `can_access_folder` pro Dokument.
- **Reindex-Löschung gefixt** ([app/ingest/pipeline.py](app/ingest/pipeline.py)):
  nutzte das wirkungslose `delete_documents(document_ids=[doc_id])` → alte
  Chunks blieben nach Reindex im Index (Split-Brain, gleiche Klasse wie oben).
  Gemeinsame Funktion `delete_qdrant_chunks` in
  [app/pipelines/vector_ops.py](app/pipelines/vector_ops.py) (Filter `meta.doc_id`).
- **OAuth-Härtung** ([app/mcp_server/oauth_routes.py](app/mcp_server/oauth_routes.py)):
  `redirect_uri` wird jetzt gegen die registrierten Client-URIs validiert
  (`_validate_redirect_uri`, vorher toter Code → Auth-Code-Phishing); Login-Seite
  HTML-escaped + CSP (reflektiertes XSS geschlossen).
- **Kleinere Härtung:** CORS auf echte Domain + Vite-Dev reduziert (kein
  `:8501`); Rate-Limit-/Login-Maps beschnitten (Speicher-DoS); Login-Dummy-Hash
  gegen User-Enumeration ([app/auth/users.py](app/auth/users.py)).
- **MCP-only-Rückbau** (§5): REST-Suche/`run_query`/`rag_search` entfernt →
  kleinere Angriffsfläche.

> **Live-verifiziert (2026-07-11, WSL-Audit-Umgebung `/root/audit`, alle grün):**
> - S1 (Retrieval-ACL: Key `/Steuer/` ohne folder sieht nur `/Steuer/`), S1b
>   (fremder folder abgelehnt), S2 (Export-IDOR), S3 (Segmentgrenze `/Steuer`
>   ≠ `/Steuer2025-Neukunde/`), Reindex-Qdrant-Fix (Chunk-Zahl stabil),
>   MCP-only-Rückbau (`/api/retrieve`+`/api/query` → 404, `rag_search` weg) —
>   **12/12** via `tests/verify_new.sh`.
> - S4 (OAuth: nicht-registrierte `redirect_uri` → 400 `invalid_redirect_uri`),
>   S5 (Login-`state` HTML-escaped + CSP-Header) — **8/8** via `tests/oauth_test.sh`
>   (OAuth per Compose-Override + `docker-compose.localonly.yml` app-Mount aktiviert).
>
> **Wichtig für die Audit-Umgebung:** der api-Container braucht den
> `docker-compose.localonly.yml`-Override (`./app:/app` + Port), sonst läuft der
> ins Image gebackene ALTE Code. `docker compose -f docker-compose.yml -f
> docker-compose.localonly.yml up -d`.

### Live-verifizierte Fixes aus dem Juli-Audit (2026-07-07)

- **Ordner-Zugriffskontrolle bei JEDEM Einzel-Dokument-Endpunkt.**
  [app/api/documents.py](app/api/documents.py): `get/patch/delete/chunks/download`
  und `delete_folder` rufen jetzt `_require_folder_access(ctx, folder)`. Vorher
  nur Scope-Check → IDOR: fremde Mandantenordner per doc_id les-/lösch-bar.
  Der Guard MUSS bei neuen Dokument-Endpunkten mit.
- **`can_access_folder` ist segment-grenzbewusst** ([app/auth/dependencies.py](app/auth/dependencies.py)) —
  kein nacktes `startswith` mehr (sonst matchte `/Steuer/` auch
  `/Steuer2025-Neukunde/`).
- **Qdrant-Löschung über `meta.doc_id`-Filter, NICHT über `doc.id`.**
  [app/api/documents.py](app/api/documents.py) `_delete_qdrant_chunks()`: die
  Qdrant-Punkt-ID ist ein Content-Hash ≠ Postgres-doc_id. `delete_documents(
  document_ids=[doc.id])` lief ins Leere → gelöschte Dokumente blieben im Index
  durchsuchbar (DSGVO Art. 17). Erst per Filter die echten Punkte holen, dann
  löschen.
- **Login-Rate-Limit pro E-Mail** ([app/api/auth_router.py](app/api/auth_router.py))
  und **MCP-Rate-Limit auf Identität/API-Key statt spoofbarem `X-Forwarded-For`**
  ([app/mcp_server/ratelimit.py](app/mcp_server/ratelimit.py)).
- **Prompt-Template gehärtet** ([app/pipelines/query.py](app/pipelines/query.py)):
  Kontext explizit als unvertrauenswürdige Daten gerahmt (Prompt-Injection).
  Rest-Risiko: `rag_retrieve` reicht rohen Chunk-Text an den Client — dort
  absichern.
- **pg-client im Image auf 16 gepinnt** ([app/Dockerfile](app/Dockerfile), PGDG-Repo).
  Der DB-Server ist `postgres:16`; das unversionierte Paket zog Client 17, dessen
  Dumps der 16er-Server nicht restaurieren kann. **Bei Server-Major-Upgrade den
  Client mitziehen.**
- **Qdrant-Snapshot wird ins Bind-Mount `/data/backups` heruntergeladen**
  ([app/backup/engine.py](app/backup/engine.py), Collection-Snapshot) — vorher
  lag er nur im Volume und war nach `down -v` weg. Neu:
  [scripts/restore.sh](scripts/restore.sh) + [docs/DISASTER-RECOVERY.md](docs/DISASTER-RECOVERY.md).
  Restore end-to-end getestet (Postgres + Qdrant).
- **Compose-Härtung** ([docker-compose.yml](docker-compose.yml)): Healthchecks
  für qdrant/ollama, `depends_on: service_healthy`, `deploy.resources.limits`
  für alle Services (auf Ziel-VPS an reale Hardware anpassen); qdrant/ollama auf
  konkrete Versionen gepinnt (kein `:latest`).
- **Non-Root-Container** ([app/Dockerfile](app/Dockerfile) `USER appuser` uid 1000,
  [supervisord.conf](app/supervisord.conf) ohne `user=root`). **Bind-Mounts
  `/data/uploads` + `/data/backups` müssen auf dem Host uid 1000 gehören**
  (`chown -R 1000:1000`, Playbook #8), sonst EACCES.
- **`/docs`+`/openapi.json` standardmäßig aus** ([app/main.py](app/main.py),
  `DOCS_ENABLED` in Settings; default false).
- **QueryLog-Retention** ([app/backup/engine.py](app/backup/engine.py)
  `cleanup_old_query_logs`, `QUERY_LOG_KEEP_DAYS`=90; läuft im Nachtlauf).
- **Dedup pro Ordner statt global** ([app/db/models.py](app/db/models.py)
  `uq_folder_doc_hash`, [app/ingest/pipeline.py](app/ingest/pipeline.py),
  Migration in [app/db/session.py](app/db/session.py)): identischer Inhalt darf
  jetzt in mehreren Ordnern liegen.
- **Reranker per `RERANK_ENABLED` schaltbar** ([app/config.py](app/config.py)) und
  **CPU-only Torch** ([app/Dockerfile](app/Dockerfile)) — Image von ~10 GB auf
  ~3,6 GB (kein CUDA-Stack mehr). Chunking-Label ist jetzt ehrlich `structural`.
- **OAuth-Scope wird gegen Whitelist geklemmt** ([app/mcp_server/oauth_routes.py](app/mcp_server/oauth_routes.py)).
- **Login akzeptiert reservierte TLDs** ([app/api/schemas.py](app/api/schemas.py),
  `LoginRequest.email: str`) — interne Domains (.local/.internal) sperren den
  Admin nicht mehr aus. Zero-Chunk-Upload → 422 statt 500.
- **Off-Site-Backup auf externe Festplatte** (`scripts/offsite*`): zentrale
  Config [scripts/offsite.conf](scripts/offsite.conf) (Label/GPG/Retention — nur
  hier ändern, kein Reinstall). Manuell: [scripts/offsite-backup-now.sh](scripts/offsite-backup-now.sh).
  Automatisch beim Anstecken via [scripts/install-offsite-autotrigger.sh](scripts/install-offsite-autotrigger.sh)
  (generische udev-Regel → systemd → [rag-offsite-handler.sh](scripts/rag-offsite-handler.sh)
  → [backup-to-external.sh](scripts/backup-to-external.sh); mount→verifizierte
  Kopie→aushängen). Verweigert Schreiben, wenn Ziel kein echter Mountpoint ist.
  Doku: [docs/DISASTER-RECOVERY.md](docs/DISASTER-RECOVERY.md).

**Noch offen (Prozess/Infra bzw. Entscheidung):** externe Platte am Host mit
Label `RAG-BACKUP` einrichten + `install-offsite-autotrigger.sh` einmalig laufen
lassen; Verschlüsselung at-rest der Volumes; `OLLAMA_NUM_PARALLEL` für echten
Mehrbenutzer-Durchsatz erhöhen (RAM-abhängig); automatisierte Test-Suite.
(**Erledigt 2026-07-13:** `os.environ` in `mcp_server/oauth.py` → `settings()`;
siehe §15 zur OAuth-Neufassung.)

**Testartefakte** aus dem Audit (Live-Prüfskripte für IDOR/Löschung/Rate-Limit/
Backup-Restore/Dedup) liegen außerhalb des Repos in der WSL-Audit-Umgebung — bei
Bedarf als Grundlage für eine echte Test-Suite (es gibt weiterhin keine, siehe §6).

### Nachtrag 2026-07-16 (Graph-ACL, Track D / M3f) — sicherheitskritisch, nicht rückbauen

Die Wissensgraph-Retrieval-Augmentierung ([app/graph/store.py](app/graph/store.py) +
`run_retrieve` in [app/pipelines/query.py](app/pipelines/query.py)) darf **niemals**
zum ACL-Umgehungspfad werden. Die Regeln (verifiziert, siehe Änderungslog):

- **Sichtbarkeit = Schnittmenge, nie Vereinigung.** `visible_doc_nodes(folder_paths)`
  bildet die Document-Nodes gegen die aufgelöste Caller-ACL. **Jede** Graph-
  Augmentierung (Fastpath, PPR) darf Docs **ausschließlich** aus dieser Menge
  zurückgeben. Neue Graph-Features hier einhängen — nicht am `visible`-Set vorbei.
- **PPR läuft über den ACL-restringierten Subgraph** (Entity-Nodes ∪ *sichtbare*
  Docs). Unsichtbare Docs sind gar nicht im Subgraph → über eine `near_dup`/
  `similar_to`-Kante ist **kein** fremdes Doc erreichbar (Leck-Test bestanden).
  Ein „einfacher" Umbau, der PPR über den vollen Graph laufen und erst am Ende
  filtern lässt, reißt genau dieses Leck auf — nicht tun.
- **Sanitize-on-Serialize ist die letzte Schranke.** `_sanitize_chunks` verwirft
  jeden Chunk außerhalb der ACL (Defense-in-Depth), `_scrub_cross_refs` nullt
  `superseded_by`, wenn es aus der ACL hinauszeigt (einziges doc-übergreifendes
  Feld), `_apply_content_budget` deckelt distinct doc_ids. Bei neuen Feldern, die
  auf andere Docs zeigen, den Scrub erweitern.
- **Getrennte ACL-Semantiken bleiben getrennt** (§4/folders.py): Bearer (leer=alles)
  vs. User/OAuth fail-safe (leer=nichts). Der Graph erbt die vom Aufrufer bereits
  aufgelösten `folder_paths` — er trifft **keine** eigene ACL-Entscheidung.

## 14. Büro-Brain — Retrieval-Qualität (2026-07-13)

Ausbau für den Einsatz als Wissens-Brain (Normen/Standards/Anleitungen). Alles
live in der WSL-Umgebung verifiziert.

- **Hybrid-Retrieval (dense + BM25-sparse).** `retrieval.hybrid` ist jetzt echt:
  [query.py](app/pipelines/query.py) `_retrieve_only_inner` nutzt bei `hybrid=True`
  den `QdrantHybridRetriever` (dichtes Ollama-`bge-m3` + sparse `Qdrant/bm25` via
  fastembed, server-seitige RRF-Fusion). Der Sparse-Anteil trifft **exakte**
  Normnummern/§/Codes, die dense verwäscht. Ingest schreibt beide Vektoren
  ([pipeline.py](app/ingest/pipeline.py) `_embed_and_store`); der Store läuft mit
  `use_sparse_embeddings=True` ([factory.py](app/pipelines/factory.py)).
  **Collection-Schema-Wechsel** → bei Migration `reset_collection()` +
  `reindex_all()` (Admin-Endpoint `POST /api/reindex-all?reset=true`). Neue
  `Qdrant/bm25`-Modelldateien lädt fastembed beim ersten Aufruf.
- **Reranker default AN** ([config.py](app/config.py) `rerank_enabled=True`).
  ⚠️ **RAM:** `BAAI/bge-reranker-v2-m3` (~2,4 GB) wird beim ersten `rag_retrieve`
  in den RAM geladen — mit dem alten **2-GB**-Container-Limit OOM-killt der Kernel
  den Prozess (SIGKILL, Request hängt). Deshalb api-`deploy.resources.limits.memory`
  auf **4 GB** ([docker-compose.yml](docker-compose.yml)). Bei RAM-Knappheit
  `RERANK_ENABLED=false`.
- **Strukturierte Metadaten** ([ingest/metadata_extract.py](app/ingest/metadata_extract.py),
  lokales Qwen 3B, robustes autotag-Muster): `doc_type`, `norm_id`, `doc_version`,
  `issued_date`, `issuer`, `language` → neue nullable Document-Spalten (Migration
  in [db/session.py](app/db/session.py)) **und** Qdrant-Payload. Deterministische
  Regel: `norm_id` erkannt ⇒ `doc_type='norm'` (das 3B verwechselt sonst gern).
  Filter in `rag_retrieve`: `doc_type`, `language`, `only_current`.
- **Versions-/Ablöse-Logik.** Beim Ingest: gleiche `norm_id`, Jahresvergleich
  (`version_year`) → ältere Fassung wird `valid_status='superseded'` +
  `superseded_by`. Retrieval reichert `outdated`/`superseded_by` **frisch aus
  Postgres** an ([query.py](app/pipelines/query.py) `_annotate_status`) — nicht aus
  dem (veraltbaren) Qdrant-Payload. Heuristik, kein Normenregister.
- **Zitier-Disziplin.** Jeder Chunk trägt ein fertiges `citation` (Datei, Seite,
  `section_path`) + `section_path`-Breadcrumb ([chunker.py](app/ingest/chunker.py)
  pflegt eine Überschriften-Kette aus DOCX-Heading-Level bzw. PDF-Gliederungs-
  nummern). `rag_retrieve`-Docstring weist den Client an, damit zu zitieren und
  bei `outdated` auf die neuere Fassung hinzuweisen.

**Noch offen (bewusst):** Connectors (SharePoint/Netzlaufwerk/Email-Ingest),
weitere Formate (pptx/csv/Bilder), Feedback-Loop, echtes Eval-Gold-Set. `language`
bleibt beim 3B oft `null`. Bulk-`reindex_all` kann die Ablöse-Heuristik temporär
inkonsistent lassen (Reihenfolge) — für sauberen Stand einzeln re-ingesten.

## 15. OAuth für MCP — saubere Neufassung (2026-07-13)

OAuth 2.1 + PKCE für Claude.ai-Connector, **standardmäßig AN** (kein Extra-Env).
Live in WSL end-to-end verifiziert (Roundtrip + 16-Fälle-Angriffsmatrix + Persistenz
über Neustart, **27/27** via `tests/oauth_verify.sh`). Behebt drei echte Defekte
der alten Umsetzung: leere `iss`/`aud`, ephemere SQLite (nach Redeploy weg),
`os.environ`+fester Einzel-User.

- **Identität = echte UiUser.** Login im `/oauth/authorize`-Formular geht gegen
  `authenticate_user()` ([auth/users.py](app/auth/users.py)) — dieselben Accounts
  wie die Admin-UI. Token-`sub` = UiUser-UUID. Kein separater OAuth-User mehr
  (`OAUTH_USER_EMAIL`/`OAUTH_PASSWORD_HASH` entfallen).
- **Config nur über `settings()`** ([config.py](app/config.py)): `oauth_enabled`
  (default true), `oauth_jwt_secret` (leer ⇒ `app_secret_key`), TTLs; abgeleitet
  `oauth_issuer`/`oauth_resource` aus `rag_domain` → `iss`/`aud` **stimmen** mit
  Discovery + `/mcp`-URL überein (sonst Client-Reject).
- **Storage = Postgres** ([db/models.py](app/db/models.py) `OAuthClient` +
  `OAuthRefreshToken`, via `init_db`). Überlebt Redeploys, im pg_dump. Auth-Codes
  kurzlebig in-memory. Kein SQLite mehr.
- **Token → Principal** ([mcp_server/server.py](app/mcp_server/server.py)
  `_OAuthPrincipal`): gültiges JWT wird bei JEDEM Request frisch zum UiUser
  aufgelöst (gelöschter User ⇒ 401). Voller Zugriff (alle UiUser sind Admins);
  **Ordner-ACL später genau hier einhängen** (statt `_AllFolders()`). Duck-typed
  das `ApiKey`-Interface, `id=None` (kein FK-Bruch bei `QueryLog`).
- **Härtung:** Rate-Limits auf `/oauth/authorize` (pro Mail), `/oauth/token`
  (pro client_id) und `/oauth/register` (pro IP) via
  [auth/ratelimit.py](app/auth/ratelimit.py) — die `/oauth/*`-Routen liefen vorher
  an der MCP-Rate-Limit-Middleware **vorbei**. DCR-Client-Cap (`MAX_CLIENTS`),
  Audit-Logging (`oauth.*`), Nachtlauf-Cleanup abgelaufener/revoked Tokens
  ([backup/engine.py](app/backup/engine.py)). Bestehende Fixes bleiben: S256-PKCE,
  redirect_uri-Whitelist (S4), Login-XSS+CSP (S5), Scope-Clamping, HS256-Pinning,
  Refresh-Rotation mit Replay-revoke-all, strikte `aud`+`iss`-Prüfung.
- **Der statische Bearer-API-Key-Pfad bleibt** parallel bestehen (Regression grün).

## 16. Umbau 2026-07-15 — Stand, Verify-Umgebung, nächste Schritte

**Aktiver Plan: [docs/RAG-OS-MASTERPLAN.md](docs/RAG-OS-MASTERPLAN.md)** (Tracks C–F,
3-Agenten-Review eingearbeitet). Zuerst lesen, bevor an C/D/E/F/A weitergearbeitet wird.

### Fertig & E2E-verifiziert (in isolierter WSL, siehe unten)
- **Track C (Parsing-Fundament):** `app/doc_ingest/` (Docling, Parent-Child,
  Tabellen verlustfrei) · Adapter `app/ingest/docling_ingest.py` · Feature-Flag
  **`ingest_backend`** (config, `legacy`|`docling`, **default legacy**) · Branch in
  `ingest/pipeline.py` · kanonische **`DocumentChunk`**-Tabelle (Postgres=Wahrheit) ·
  Docling-Ingest-Image `app/Dockerfile.ingest` + `docker-compose.docling.yml`.
  **Der DOCX-Tabellen-Bug ist behoben** (Upload→docling→Retrieve zeigt Markdown-Tabelle).
- **Track D (Fundament):** `app/graph/canonical.py` (idempotente ID-/Normnummern-
  Normalisierung; verifiziert).

### Stand (Details im Änderungslog oben — das ist der lebende Record)
- **Masterplan-Tracks C–F durch & auf `main`:** C3b (Worker-Split), E (Mehrbenutzer/
  ACL), M0/M1.1–M1.5 (Enqueue, reindex-from-chunks, torch-freies Serving, air-gapped
  Docling, Quant, Slim/Fett-Split, Docling=Standard), M2 (Speed), **Track D komplett**
  (Graph: Modelle→L1→L2→Analyse→Retrieval-Integration+Graph-Sicherheit) und **Track F
  komplett** (M4: Ordner-Reorg aus D-Communities via `move_document()`).
- **Bewusst verschoben (out of scope, Masterplan):** L3-Entity-Layer, Connectors
  (SharePoint/Email/Netzlaufwerk), weitere Formate (pptx/csv/Bilder), Eval-Gold-Set,
  at-rest-Verschlüsselung, Offsite-Autotrigger.

### WSL-Verify-Umgebung (isoliert, reproduzierbar)
Zum Verifizieren wurde eine **eigene** WSL-Distro **`Ubuntu-22.04`** mit **nativem
Docker** aufgesetzt (getrennt von der `Ubuntu-24.04`-Audit-Umgebung — eigener
Daemon, keine geteilten Container). Klon unter **`/root/rag-os`**, `.env` mit
`RAG_DOMAIN=localhost`. Start:
```
docker compose -f docker-compose.yml -f docker-compose.localonly.yml [-f docker-compose.docling.yml] up -d
```
- `docker-compose.localonly.yml` = `./app:/app`-Mount + Port `127.0.0.1:8000`.
- `docker-compose.docling.yml` = api aus dem Docling-Image (`rag-ingest`) + `ingest_backend=docling`.
- **Stolperstein:** NIE `docker restart`/`docker start` einzeln — trennt den Container
  vom `rag-net` (Postgres unauflösbar → Crash-Loop). Immer `docker compose … up -d
  --force-recreate` bzw. `down`+`up`.

### Docling-Gotchas (aus dem C0-Spike, gehören in C3b/Prod)
- `torch` **und** `torchvision` gemeinsam aus dem CPU-Index (sonst `torchvision::nms`-Mismatch).
- Modelle vorab: `docling-tools models download -o /models/docling`; zur Laufzeit
  `PdfPipelineOptions.artifacts_path` **+ `HF_HUB_OFFLINE=1`** (sonst Runtime-Download → offline-Crash).
- `do_ocr=False` für born-digital PDFs (Norm-PDFs); Scan-PDFs brauchen vorgebackene RapidOCR-ONNX-Modelle.
- bge-m3-**Tokenizer** (HybridChunker) muss für air-gapped auch gebacken werden.

> **Sicherheit:** Alle Track-E-Fixes sind noch **offen** — bis dahin ist der
> Mehrbenutzer-Betrieb NICHT prod-tragfähig (Details Masterplan Track E).
