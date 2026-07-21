# RAG OS — lokaler Wissens-Suchknoten

Selbstgehosteter, **komplett lokaler** Retrieval-Knoten über das Wissen von Julius
Sima. Kein Cloud-Dienst, keine Server-Infrastruktur: eine **native Windows-Desktop-App**.
Sie indexiert Dokumente und macht sie über einen **MCP-Server** für jeden MCP-fähigen
KI-Client (Claude Desktop, eigene Agents …) durchsuchbar. Die Antwort formuliert immer
der Client — das System **liefert Chunks + Quellen, es antwortet nicht selbst**.

> **Quelle der Wahrheit für Architektur & Stand:** [CLAUDE.md](CLAUDE.md) (Agent-Briefing,
> Goldener Pfad, Sicherheit) + [BUILD-PLAN.md](BUILD-PLAN.md) (Meilensteine). Vollspec:
> [SPEC.md](SPEC.md). Vision: [docs/VISION.md](docs/VISION.md).

---

## Was JETZT gilt (ein Prozess, kein Docker)

- **Native Desktop-App, kein Docker/VPS.** FastAPI + MCP unter `uvicorn` auf
  `127.0.0.1`, in einer **pywebview/WebView2-Shell** ([app/desktop.py](app/desktop.py),
  Tray/Autostart/Toast). Zwei Windows-Installer (Schreiber/Leser) via PyInstaller +
  Inno-Setup ([build/](build/)).
- **LanceDB = einziger Wissensspeicher** ([app/pipelines/store.py](app/pipelines/store.py),
  im Vault) — ersetzt Qdrant **und** die Postgres-Korpus-Tabellen. Dazu ein lokales
  **`appstate.sqlite`** (Keys/Users/Log/Graph, **nicht** im Vault).
- **Embeddings: INT8-ONNX `intfloat/multilingual-e5-large`** (1024-dim, mehrsprachig),
  direkt über onnxruntime — **kein Ollama/LLM**. Reranker `bge-reranker-v2-m3` ebenfalls
  INT8-ONNX. Tagging + Graph sind **deterministisch, LLM-frei**.
- **Suche ist MCP-only, read-only** (kein OAuth/TOTP): `rag_retrieve`, `rag_overview`,
  `norm_lookup`, `rag_list_documents`, `rag_get_document`, `rag_stats`. Identität =
  statischer Bearer-API-Key; Ordner-ACL serverseitig erzwungen.
- **Admin-UI** = React/Vite-SPA ([app/frontend/](app/frontend/)), von FastAPI als
  Static Files serviert, lokaler Auto-Login (127.0.0.1). **Keine Suchseite** (Suche läuft
  über MCP); Dashboard/Dokumente/**Graph**/Keys/System/Wartung.
- **Zwei Ordnungsebenen:** `folder_path` (freier, nestbarer Pfad) + Tags (`TEXT[]`,
  cross-cutting). **Kein „Projekt"-Konzept** mehr.
- **Rollen:** *Schreiber* (Ingest + Query, Docling/torch, schreibt Vault-Versionen) vs.
  *Leser* (query-only, liest lokalen Cache am LanceDB-`current`-Tag).

## Wissensgraph

Deterministischer Graph (Dokumente ↔ Normen/Tags/Aussteller/Ordner + Ähnlichkeiten),
im Frontend als interaktive Ansicht (`/graph`). Der Schreiber baut ihn **manuell** per
Button; das Ergebnis liegt als `.ragos/graph.json` im Vault, der Leser liest sie passiv.
`GET /api/graph` ist **pro Aufrufer ACL-gefiltert** (jeder sieht nur seinen erlaubten
Subgraphen; §13-Sicherheitsmodell in [CLAUDE.md](CLAUDE.md)).

---

## Dev-Setup (Windows, nativ)

```powershell
# Backend (Python 3.14)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e "app[writer,dev]"
python app/desktop.py            # Shell  — oder:  cd app; uvicorn main:app

# Frontend (Vite)
cd app/frontend; npm install
npm run dev                      # Dev-Server
npm run build                    # -> app/ui_static/ (vom Installer mitgebacken)
```

Konfiguration über `app-settings.json` bzw. Env (`RAG_VAULT_PATH`, `RAG_SERVICE_ROLE`).
SQLite unter `%LOCALAPPDATA%\RAG-OS\appstate.sqlite`, LanceDB im Vault — **keine DB-Server**.
Kein automatisiertes Test-Setup; Verifikation über isolierte venv-/E2E-Skripte +
`ruff check app`.

## Deployment = Installer bauen

Kein Docker, kein Proxy, kein VPS. „Deployment" heißt **Installer bauen** mit
[build/build.ps1](build/build.ps1) (PyInstaller + Inno-Setup, Rollen Schreiber/Leser) und
ausführen. Alle KI-Modelle sind **gebündelt** (kein Runtime-Download). Details:
[BUILD-PLAN.md](BUILD-PLAN.md) + [CLAUDE.md](CLAUDE.md) §10.

---

## Projektstruktur (grob)

```
rag-os-app-lokal/
├── CLAUDE.md            ← Agent-Briefing + Quelle der Wahrheit
├── BUILD-PLAN.md        ← Meilenstein-/Fortschrittstabelle
├── SPEC.md              ← verbindliche Spezifikation (Was/Warum)
├── app/
│   ├── desktop.py       ← pywebview/WebView2-Shell
│   ├── main.py          ← FastAPI-Lifespan, Router, MCP-Mount, SPA-Fallback
│   ├── config.py        ← settings() (einzige Config-Quelle)
│   ├── api/             ← REST-Verwaltung (Dokumente/Keys/System/Wartung/Graph)
│   ├── mcp_server/      ← MCP-Tools (Bearer-only, read-only)
│   ├── auth/            ← API-Keys, UI-Login, kanonische Ordner-ACL (folders.py)
│   ├── pipelines/       ← LanceDB-Store, Query (Hybrid+Rerank), Embedder/Reranker (ONNX)
│   ├── ingest/          ← Parser/Chunker, Auto-Tag (deterministisch), Folder-Watcher
│   ├── doc_ingest/      ← layout-aware Parsing/Chunking (Docling)
│   ├── graph/           ← deterministischer Wissensgraph (L1/L2/Analyse + Export)
│   ├── db/              ← SQLAlchemy-Models (appstate.sqlite)
│   └── frontend/        ← React/Vite Admin-UI (-> app/ui_static/)
├── build/               ← PyInstaller-Specs + Inno-Setup + fetch-models.py
└── docs/                ← ARCHITECTURE.md (aktuell) · VISION.md · Audit-Records (historisch)
```

---

## Lizenz

MIT für den Eigencode. Bibliotheken unter ihren jeweiligen Lizenzen (LanceDB Apache 2.0,
onnxruntime MIT, Docling MIT, multilingual-e5-large MIT, bge-reranker-v2-m3 Apache 2.0).
