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
>   ersetzt Qdrant **und** die Postgres-Korpus-Tabellen.
> - **Zwei SQLite-DBs (Multi-Vault, 2026-07-22):** `credentials.sqlite` (Keys/Nutzer,
>   **lokal** pro Rechner, maschinenweit über alle Firmen) + `<vault>/.ragos/state.sqlite`
>   (Dokumente/Chunks/Graph/Logs/Jobs, **im Vault** → Firma = ein portabler Ordner).
>   `get_session()` = Vault, `get_local_session()` = Credentials. Split-Details: §4.
> - **Embeddings: INT8-ONNX `intfloat/multilingual-e5-large`** (1024-dim, mehrsprachig;
>   **nicht** bge-m3), direkt über onnxruntime (`factory.py`, Mean-Pooling + e5-Query/
>   Passage-Präfixe) — **kein fastembed mehr**; INT8 ~3,2× schneller/4× kleiner (M8g).
>   Reranker bge-reranker-v2-m3 ebenfalls INT8-ONNX.
>   **Kein Ollama/LLM** — Tagging/Graph sind deterministisch (LLM-frei).
> - **Rollen (M8e):** `writer` (Ingest+Query, Docling/torch, schreibt Vault-Versionen)
>   vs. `reader` (query-only, liest lokalen Cache am LanceDB-`current`-Tag; kein
>   Docling/torch). Umschaltung über `settings().service_role`.
> - **MCP: Bearer-only, read-only** (kein OAuth/TOTP). Tools: `rag_overview`,
>   `rag_retrieve`, `norm_lookup`, `rag_list_documents`, `rag_get_document`(+Volltext),
>   `rag_stats`. UI hat lokalen **Auto-Login** (`local_ui_autologin`, 127.0.0.1).
> - **Publish/Versionierung** über LanceDB-Tags (`current`/`prev`) + Leser-Cache
>   (`app/pipelines/publish.py`). Backup = Vault-Kopie + appstate (`backup/engine.py`).
> - **Packaging (`build/`):** zwei Windows-Installer (Schreiber ~1,8 GB voll /
>   Leser ~1,1 GB — nach INT8-Embedder M8g) via PyInstaller + Inno-Setup; beide gebaut
>   & E2E-verifiziert.
>   **Alle KI-Modelle sind GEBÜNDELT** (kein Runtime-Download): Query (e5-large + Reranker,
>   beide Installer) + Ingest (Docling Layout/TableFormer + e5-Tokenizer, nur Schreiber).
> - **Gelöscht:** Docker/Compose/Caddy, `worker.py`, OAuth/TOTP, pg_dump/Qdrant-Backup,
>   Postgres-/Qdrant-/Ollama-/Haystack-Deps.

> **Änderungslog:**
> - 2026-07-22 — **Multi-Vault (Firmen-Trennung) + Graph-Viz-ACL.** (1) **Wissensgraph-
>   Visualisierung** (`/graph` + `GET /api/graph`) **per-User-ACL-gefiltert** (Schnittmenge,
>   §13 — 19/19 verifiziert); Lesequelle `.ragos/graph.json` im Vault (manueller Rebuild,
>   Writer-only). Bug gefixt: `analyze_graph` ZeroDivisionError bei isolierten Communities
>   (`networkx.conductance`, Volumen 0). (2) **Zwei-DB-Split (§4):** `credentials.sqlite`
>   (Keys/Nutzer, lokal, firmenübergreifend) + `<vault>/.ragos/state.sqlite` (Content, im
>   Vault → Firma = portabler Ordner). Neue `LocalBase`, `get_local_session()` nur für Auth,
>   4 DB-übergreifende FKs → Audit-UUIDs, Vault-DB mit Rollback-Journal (SMB). Einmal-
>   Migration Alt-appstate ([db/migrate.py](app/db/migrate.py), idempotent, → `.migrated`).
>   Tray-Untermenü „Vault (Firma)" + Vault-Anzeige auf der System-Seite. Reader zieht
>   `state.sqlite` in den Cache. **Live verifiziert** (echtes Backend, 2 Vaults parallel:
>   saubere Trennung, 3 Docs migriert, kein Reimport). Auf `main` bis `c109afc`.
> - 2026-07-21 — **Doku auf nativen Stand + toten Spike-Code entfernt.** `spike/` gelöscht
>   (Wegwerf-Spikes); `docs/DEPLOYMENT.md`+`DISASTER-RECOVERY.md` (tote Docker/Qdrant-Infra)
>   raus; README/ARCHITECTURE neu auf nativen Stack; SPEC/BUILD-PLAN aktualisiert; Audit-
>   Records historisch markiert. Auf `main` (`27d4bea`).
> - 2026-07-21 — **M8g: Ingest-Speed (INT8-Embedder) + Tag-Fix.** Zwei Nutzer-Bugs:
>   Ingest „ewig lang" + Tags komplett falsch. **Diagnose (hart gemessen):** der Engpass
>   ist das **Embedding** (~1 s/Chunk, e5-large fp32 auf CPU = **95 %** der Ingest-Zeit;
>   EStG: 474 s embed vs. 33 s parse vs. ~0 Rest). Parser (Docling/Legacy) und „GPU-für-
>   Docling" waren Irrwege (Docling batcht Seiten einzeln → nur 3× GPU); **GPU-Embedding
>   via onnxruntime-gpu scheitert auf Py3.14** (CUDA-Provider lädt nicht, still CPU-Fallback).
>   **Fix (Option A):** e5-large **INT8-quantisiert** (`onnxruntime.quantize_dynamic`); der
>   Embedder läuft jetzt **direkt über onnxruntime** (`pipelines/factory.py`, Mean-Pooling+
>   L2-Norm, analog Reranker) statt fastembed → **~3,2× schneller** auf CPU, Modell **4×
>   kleiner** (561 MB statt 2,2 GB), Retrieval-Qualität intakt (Vektor-Treue 0,99, Query↔
>   Passage-Cosine unverändert). Gebündelt als `models/embedder` (`fetch-models.py`
>   quantisiert im Build); **fastembed-Modell aus BEIDEN Installern raus → beide ~1,6 GB
>   kleiner**; Reader-Installer nimmt `embedder` statt `fastembed`. **Tag-Fix:**
>   `generate_tags` nahm nur die ersten 4000 Zeichen (= BGBl-Novellenkopf) → Müll-Tags
>   (`bgbl/xvii/bundesgesetz`); jetzt ganzer Text + Boilerplate-/Römisch-Filter → relevante
>   Tags (MRG: `vermieter/mieter/wohnung/hauptmietzins`). **Verifiziert:** E2E EStG-Ingest
>   **509 s → 217 s**, Retrieval korrekt + ordner-scharf; `ruff` grün.
> - 2026-07-21 — **M8f: Docling-Modelle gebündelt (Erststart-Race behoben).** Beim ersten
>   echten Install-Test scheiterte JEDER erste Ingest mit „Missing safe tensors file":
>   Docling lud das Layout-Modell (`docling-layout-heron`) zur Laufzeit vom HF-Hub, und der
>   Ingest rannte mit dem Download um die Wette; das alte `offline=True` griff nicht (Flags
>   zu spät gesetzt, huggingface_hub cached sie beim Import). **Fix (Option A):** Docling
>   Layout+TableFormer + e5-Tokenizer werden jetzt beim Build gebacken (`build/fetch-models.py`)
>   und vom Schreiber-Installer nach `%LOCALAPPDATA%\RAG-OS\models\{docling,e5-tokenizer}`
>   gelegt; `run_docling` zeigt Docling per `artifacts_path` + lokalem Chunk-Tokenizer explizit
>   darauf (`config.docling_artifacts_dir`/`chunk_tokenizer_dir`); `HF_HUB_OFFLINE`/
>   `TRANSFORMERS_OFFLINE` werden am Prozessstart in `main.py` gesetzt (vor jedem HF-Import).
>   Reader-Installer excludet Docling/Tokenizer (query-only bleibt schlank). Kein Runtime-
>   Download mehr, air-gapped, kein Race. Nebenbei: `rapidocr-onnxruntime`-Pin `>=1.3`→`>=1.2`
>   (kein cp314-Wheel für 1.3+; OCR ist aus). **Voll verifiziert (isoliert, offline):** beide
>   zuvor gescheiterten PDFs ingesten sauber — MRG (307 Chunks, `/mietrecht/`) + EStG 1988
>   (1339 Chunks inkl. Tabellen, `/steuer/`) = **1646 Chunks in LanceDB**, kein safetensors-
>   Fehler; **Retrieval offline korrekt + ordner-scharf** (Mietzins-Query→MRG §16, Steuer-
>   Query→EStG „Werbungskosten §16"); Frozen-Build bootet + warmt offline (Embedder+Reranker).
>   `ruff` grün. **Beide Installer neu gebaut** (Schreiber 3,5 GB nach RapidOcr-Trim, Leser
>   2,6 GB). Auf `main` (`ba32b28`). Der frühere WebView2-Zyklus im headless Test war ein
>   Harness-Artefakt (kein Produktfehler).
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
> - **2026-07-13 bis -07-16 — Docker-Ära (M1–M4, Track C–F, OAuth, Büro-Brain) —
>   HISTORISCH.** Der damalige VPS-Docker-Stack (Qdrant + Postgres + Ollama +
>   Haystack, torch-freies Serving-Image, Docling-Zwei-Container, OAuth 2.1 + PKCE,
>   Wissensgraph L1/L2/Analyse, Ordner-Reorg, Retrieval-Härtung) wurde Ende Juli
>   durch den nativen Umbau (LanceDB, INT8-ONNX, ein Prozess) ersetzt. Detail in
>   der git-Historie; noch gültige Prinzipien stehen in §4/§13/§14.
> - **2026-07-07/-11 — Sicherheits-/Produktionsaudits + Sanierung** (Ordner-ACL,
>   IDOR, serverseitige Retrieval-ACL — Prinzipien in §13). „Projekt"-Konzept
>   entfernt (nur Ordner + Tags), Streamlit → React-Frontend.

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
4. [app/ingest/pipeline.py](app/ingest/pipeline.py) — Datei → LanceDB (Backend-Weiche
   `ingest_backend`: `legacy` = PyMuPDF/python-docx + struktureller Chunker, `docling`
   = layout-aware; schreibt Chunks kanonisch nach SQLite `DocumentChunk`, dann LanceDB)
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
- **Blockierende Modell-/Store-Calls immer in `asyncio.to_thread`** — Embedding
  (ONNX), Docling, Reranker, LanceDB — siehe
  [app/pipelines/query.py](app/pipelines/query.py) und
  [app/ingest/pipeline.py](app/ingest/pipeline.py). Sonst blockiert der Eventloop
  während der Modell-Inferenz.

## 4. Mental Model für Daten

**Kein "Projekt"-Konzept mehr.** Bis Anfang Juli 2026 gab es eine dritte
Dimension "Projekt" (= eigene Qdrant-Collection, eigene API-Key-Whitelist).
Sie wurde vollständig entfernt und durch reine Ordner-Hierarchie ersetzt
(siehe Commits `578415b`, `4b94bd6`, `e356c59` — "Aura Explorer-Redesign").
Falls ein alter Vorschlag/Plan noch von `project`, `allowed_projects` oder
`projects_config()` spricht: das ist Alt-Wissen, nicht nachbauen.

Aktuell zwei orthogonale Dimensionen:

- **`folder_path`** = freier Text-Pfad (organisch, entsteht beim Upload,
  beliebig nestbar, VS-Code-artiger Explorer im Frontend). Filter im Store über
  die `folder`/`folder_path`-Spalte (LanceDB-WHERE). Zugriffskontrolle:
  `ApiKey.allowed_folders` + die **kanonische** ACL in
  [app/auth/folders.py](app/auth/folders.py) (`is_within` / `key_allows_folder` /
  `accessible_folder_paths`). `AuthContext.can_access_folder()` ist ein dünner
  Wrapper darum — siehe §13.
- **Tags** = TEXT[] (cross-cutting). Manuell vom User oder **deterministisch**
  beim Ingest vorgeschlagen ([app/ingest/autotag.py](app/ingest/autotag.py), kein LLM).

Es gibt genau **eine** LanceDB-Tabelle `chunks`
([app/pipelines/store.py](app/pipelines/store.py)) für alle Dokumente — keine
Collection/Tabelle pro irgendwas.

SQLite ([app/db/models.py](app/db/models.py)) ist Single-Source-of-Truth für
"was haben wir?", LanceDB für "wo steht es?". Driften die zwei auseinander
(Doc in SQLite, kein Chunk in LanceDB) → Bug, nicht Feature.

**Zwei-DB-Split (Multi-Vault, 2026-07-22).** SQLite ist auf **zwei** Dateien mit
**zwei** `DeclarativeBase` verteilt ([models.py](app/db/models.py) `LocalBase` vs. `Base`):
- **`credentials.sqlite`** (lokal, `%LOCALAPPDATA%`): `ui_users` + `api_keys`. Nie im
  Vault/NAS, maschinenweit über **alle** Firmen-Vaults geteilt. Zugriff: `get_local_session()`.
- **`<vault>/.ragos/state.sqlite`** (im Vault): aller Content (documents/chunks/graph/logs/
  jobs). Reist mit dem Vault → Firma = ein portabler, selbst-beschreibender Ordner. Zugriff:
  `get_session()` (Default, unverändert für fast alles).

Regeln: **neuen Keys/Nutzer-Code auf `get_local_session()`**, alles andere `get_session()`.
**Keine DB-übergreifenden FKs** (documents.uploaded_by / query_log.api_key_id/user_id sind
reine Audit-UUIDs). Vault-DB nutzt **Rollback-Journal statt WAL** (SMB-tauglich, Single-Writer).
Vault-Wechsel: Tray „Vault (Firma)" → Neustart. Einmal-Migration Alt-`appstate.sqlite` →
Split in [db/migrate.py](app/db/migrate.py) (idempotent, Alt-DB → `.migrated`). Reader: liest
`state.sqlite` aus dem lokalen Cache (Phase 2, noch offen).

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
System, Wartung, Suggest. Embeddings laufen lokal als **INT8-ONNX** (e5-large,
`pipelines/factory.py`, kein Ollama/LLM); Auto-Tagging ist **deterministisch**
([app/ingest/autotag.py](app/ingest/autotag.py) — Termfrequenz + Boilerplate-/
Römisch-Filter über dem ganzen Text, kein LLM). Nur die *Antwort-Generierung*
zur Suche fehlt bewusst (retrieve-only).

## 6. Dev-Workflow auf Windows

**Nativ, kein Docker.** venv anlegen, `pip install -e app[writer,dev]`
(Python 3.14), dann `python app/desktop.py` (Shell) oder `uvicorn main:app`
aus `app/`. Konfiguration über `app-settings.json` bzw. Env (`RAG_VAULT_PATH`,
`RAG_SERVICE_ROLE`). Zwei SQLite-DBs — `%LOCALAPPDATA%\RAG-OS\credentials.sqlite`
(Keys/Nutzer, lokal) + `<vault>/.ragos/state.sqlite` (Content, im Vault; §4) — plus
LanceDB (im Vault). **Keine DB-Server.**

- Python-Edit → App neu starten (bzw. uvicorn-Reload).
- Frontend-Edit ([app/frontend/src](app/frontend/src)) → `npm run dev` (Vite) / `npm run build`.
- **Kein automatisiertes Test-Setup** (kein `tests/`, kein pytest). Verifikation:
  isolierte venv-/E2E-Skripte + `ruff check app`. Tests gegen echtes SQLite/LanceDB.

## 7. Anti-Goals

Was nicht zu tun ist, auch wenn es naheliegt:

- **Keine neue LLM-Antwort-Generierung im Tool ausbauen.** Das System ist
  Retrieve-only. Wer Antworten will, formuliert sie im konsumierenden Client.
- **Keine neuen Abstraktionen ohne konkreten zweiten Use-Case.** Drei ähnliche
  Code-Stellen schlagen eine vorzeitige Abstraktion.
- **Keine Mocks für DB/Store in Tests.** Gegen echtes SQLite + echte LanceDB
  testen (billig, lokal) — Mock-Tests, die nicht den echten Pfad treffen, geben
  falsche Sicherheit.
- **Keine `--no-verify`-Commits.** Wenn Hooks fehlschlagen: Ursache fixen,
  nicht überspringen.
- **Kein `os.environ` umgehen** der `settings()`-Schicht.
- **Keine eigene ACL-Logik.** Ordner-Zugriff nur über [app/auth/](app/auth/)
  (`folders.py`/`dependencies.py`), nie nacktes `startswith` (§13).

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

## 10. Deployment = Installer bauen (kein Docker mehr)

Kein Docker-Stack, kein Edge-Proxy, kein VPS — die App ist eine native
Windows-Desktop-App. „Deployment" = **Installer bauen** mit `build/build.ps1`
(PyInstaller + Inno-Setup, zwei Rollen Schreiber/Leser) und ausführen. Details:
**[BUILD-PLAN.md](BUILD-PLAN.md)** + Statusbanner oben. Alle KI-Modelle sind
gebündelt (kein Runtime-Download).

### Ingest-Backend (config `ingest_backend`, Default `docling`)

- **`docling`** — layout-aware, Tabellen verlustfrei, gebündelte Modelle, langsamer.
- **`legacy`** — PyMuPDF/python-docx, schnell, keine Tabellen-Struktur.
- Der **Embedding-Schritt** (INT8-e5-large) dominiert die Ingest-Zeit, nicht der
  Parser (M8g). Rollback-Ventil: `INGEST_BACKEND=legacy`. Bestandsdaten bleiben
  (kein Auto-Reindex).

## 11. (entfernt) Edge-Proxy / VPS

Kein Reverse-Proxy, kein VPS, keine öffentliche Erreichbarkeit — die App läuft
rein lokal auf `127.0.0.1` in der WebView2-Shell. (Historisch: `edge-caddy`
unter `rag-os.sima.business`; in der git-Historie.)

## 12. Frontend (React)

Seit Mai 2026 läuft die Admin-UI ("Aura Explorer", VS-Code-artiger
Ordnerbaum) als React/Vite/TypeScript-App. Streamlit ist komplett weg — der
tote `app/ui/`-Ordner und die `streamlit`/`pandas`-Dependencies wurden
2026-07-11 entfernt. Die UI ist reine Admin-Oberfläche (Dashboard, Dokumente,
Keys, System, Wartung); **keine** Suchseite mehr (Suche läuft über MCP, §5).

- Source: [app/frontend/src](app/frontend/src) (**nicht** `frontend/` im Repo-Root)
- Built: `app/ui_static/` (gitignored) — von [app/main.py](app/main.py) als
  Static Files + SPA-Fallback auf `/` serviert (lokaler Auto-Login, 127.0.0.1)
- Dev-Server: `cd app/frontend && npm run dev` (Vite); Build: `npm run build`
  (`tsc && vite build`) → `app/ui_static/`; der Installer backt es via
  `build/build-frontend.ps1` mit.

## 13. Sicherheits-Audit Juli 2026 — behobene Befunde & Absicherung

Vor der geplanten Mehrbenutzer-Einführung wurde ein vollständiges Audit gefahren.

> **Lesehinweis (nach dem nativen Umbau):** Die Infrastruktur-Bezüge unten sind
> **historisch** (Qdrant→LanceDB, Postgres→SQLite, OAuth/TOTP entfernt, kein
> Docker/VPS). Die **Sicherheits-PRINZIPIEN gelten unverändert weiter** — v.a.
> die kanonische Ordner-ACL ([app/auth/folders.py](app/auth/folders.py), nie
> eigenes `startswith`), die serverseitige Retrieval-ACL
> ([app/pipelines/query.py](app/pipelines/query.py)), IDOR-Schutz pro Dokument
> und die Graph-ACL (§13-Nachtrag 2026-07-16). **Diese nicht rückbauen.**

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
  [scripts/restore.sh](scripts/restore.sh) + docs/DISASTER-RECOVERY.md (2026-07-21 entfernt, VPS-Ära).
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
  Doku: docs/DISASTER-RECOVERY.md (2026-07-21 entfernt, VPS-Ära).

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

### Nachtrag 2026-07-21 (Graph-**Visualisierung** `GET /api/graph`) — sicherheitskritisch, nicht rückbauen

Der Graph-Viz-Endpunkt ([app/api/system.py](app/api/system.py) `get_graph`) war anfangs
**admin-only, aber ungefiltert** (lieferte alle Dateinamen/Ordner/Tags an jeden
admin-scoped Key → §13-Verstoß). Jetzt **per-User-ACL**, nach denselben Regeln:

- **Eine flache Lesequelle:** `.ragos/graph.json` im Vault ([config.py](app/config.py)
  `graph_json_path`), vom Schreiber beim **manuell ausgelösten** Rebuild geschrieben
  ([app/graph/refresh.py](app/graph/refresh.py) `_export_graph_json`, Writer-only). Leser
  **lesen sie nur** — kein Sync, kein Import, keine appstate-Graph-Tabellen auf dem Leser.
  Rebuild auf dem Leser ist per 409 gesperrt (würde die gute Datei leer überschreiben).
- **Sichtbarkeit = Schnittmenge** über die puren Prädikate `key_allows_folder` /
  `user_allows_folder` ([auth/folders.py](app/auth/folders.py), **keine** DB-Query →
  identisch auf Schreiber/Leser): sichtbare Docs → daran hängende Entities → Kante nur
  wenn **beide** Endpunkte behalten. Eine `near_dup`/`similar_to`/`supersedes`-Kante über
  die Ordnergrenze offenbart **kein** fremdes Doc. Erst ACL, **dann** `types`/`limit`.
  Fail-safe: leere ACL → leere Antwort. **Verifiziert** (isoliertes venv, 19/19:
  `/steuer/`-User sieht kein `/mietrecht/`, keine Cross-`near_dup`-Kante, Segmentgrenze
  `/steuer` ≠ `/steuer2025-fremd/`). Neue Felder, die auf andere Docs zeigen → hier
  mitfiltern, nicht am `visible`-Set vorbei.

## 14. Büro-Brain — Retrieval-Qualität

Ausbau für den Einsatz als Wissens-Brain (Normen/Standards/Anleitungen). Die
**Konzepte** gelten weiter; die Infrastruktur ist heute LanceDB + INT8-ONNX
(nicht mehr Qdrant/Ollama).

- **Hybrid-Retrieval (dense + FTS/BM25).** [query.py](app/pipelines/query.py)
  `run_retrieve` fusioniert **dichtes INT8-e5-large** (`factory.embed_query`) mit
  LanceDBs **FTS** (BM25) via RRF. Der lexikalische Anteil trifft **exakte**
  Normnummern/§/Codes, die dense verwäscht.
- **Reranker default AN** ([config.py](app/config.py) `rerank_enabled=True`):
  `BAAI/bge-reranker-v2-m3` als **INT8-ONNX** ([pipelines/reranker.py](app/pipelines/reranker.py),
  onnxruntime, kein torch). Bei RAM-Knappheit `RERANK_ENABLED=false`.
- **Strukturierte Metadaten** ([ingest/metadata_extract.py](app/ingest/metadata_extract.py),
  **deterministisch, kein LLM**): `doc_type`, `norm_id`, `doc_version`,
  `issued_date`, `issuer`, `language` → Document-Spalten (SQLite) **und**
  Chunk-Payload (LanceDB). Regel: `norm_id` erkannt ⇒ `doc_type='norm'`. Filter in
  `rag_retrieve`: `doc_type`, `language`, `only_current`.
- **Versions-/Ablöse-Logik.** Beim Ingest: gleiche `norm_id`, Jahresvergleich
  (`version_year`) → ältere Fassung `valid_status='superseded'` + `superseded_by`.
  Retrieval reichert `outdated`/`superseded_by` **frisch aus SQLite** an
  (`_annotate_status`) — nicht aus dem (veraltbaren) Store-Payload. Heuristik.
- **Zitier-Disziplin.** Jeder Chunk trägt ein fertiges `citation` (Datei, Seite,
  `section_path`) + `section_path`-Breadcrumb ([chunker.py](app/ingest/chunker.py)).
  Der `rag_retrieve`-Docstring weist den Client an, damit zu zitieren und bei
  `outdated` auf die neuere Fassung hinzuweisen.

**Noch offen (bewusst):** Connectors (SharePoint/Netzlaufwerk/Email-Ingest),
weitere Formate (pptx/csv/Bilder), Feedback-Loop, echtes Eval-Gold-Set,
Multi-PC-Verteilung des Vaults (NAS).

## 15. (entfernt) OAuth für MCP

OAuth ist raus (M8): MCP ist **Bearer-only, read-only** (kein OAuth/PKCE/TOTP,
keine OAuth-Tabellen). Identität = statischer API-Key (`ApiKey`), Ordner-ACL wie
in §4/§13. Die UI hat lokalen Auto-Login (`local_ui_autologin`, 127.0.0.1).

## 16. (historisch) Umbau Docker → nativ

Der gesamte Umbau (Docker/Qdrant/Postgres/Ollama/Haystack/OAuth → native App,
LanceDB, INT8-ONNX) ist abgeschlossen und im **Statusbanner oben + Änderungslog
(M1–M8g)** dokumentiert. Die frühere WSL/Docker-Verify-Umgebung, die
Docling-Gotchas des C0-Spikes und der Masterplan
([docs/RAG-OS-MASTERPLAN.md](docs/RAG-OS-MASTERPLAN.md)) sind historisch — der
Docling-Offline-Betrieb ist heute in `build/fetch-models.py` + `doc_ingest/` gelöst.
