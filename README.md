# SIMA RAG-System

> ⚠️ **STARK VERALTET (Stand 2026-07-20).** Dieses README beschreibt den alten
> **VPS-Docker-Stack**. Die App ist inzwischen eine **native, Docker-freie
> Windows-Desktop-App** — der gesamte Umbau (M1–M8) ist fertig und beide Installer
> sind gebaut. **Quelle der Wahrheit: [BUILD-PLAN.md](BUILD-PLAN.md) + [CLAUDE.md](CLAUDE.md).**
> Kurz, was JETZT gilt (statt des Textes unten):
> - **Kein Docker/Postgres/Qdrant/Ollama/Haystack.** Ein `uvicorn`-Prozess in einer
>   **pywebview/WebView2-Shell** (`app/desktop.py`), zwei Windows-Installer (Schreiber/Leser).
> - **LanceDB = einziger Wissensspeicher** (im Vault) + lokales `appstate.sqlite`.
> - **Embeddings: ONNX `intfloat/multilingual-e5-large`** (kein Ollama). Reranker als INT8-ONNX.
> - **MCP: Bearer-only, read-only** (`rag_overview`/`rag_retrieve`/`norm_lookup`/…). UI mit
>   lokalem Auto-Login (127.0.0.1). Tagging/Graph **deterministisch, LLM-frei**.
> - Kein „Projekt"-Konzept (nur Ordner + Tags), Admin-UI ist React/Vite (kein Streamlit).

Selbst-gehostetes, komplett lokales Retrieval-Augmented-Generation-System
für die Wissens-Bestände von Julius Sima. Exponiert einen MCP-Server und eine
Verwaltungs-REST-API. Alles läuft auf dem eigenen VPS, nichts verlässt den Server.

---

## Was das System ist — und was nicht

**Das System ist** ein Baukasten, der Dokumente indexiert und durchsuchbar macht,
damit jedes Deiner KI-Projekte (Claude Desktop, n8n, eigene Agents, Cursor, …)
über ein einheitliches MCP-Tool auf Dein Wissen zugreifen kann.

**Das System ist nicht** eine Chat-Oberfläche wie ChatGPT. Die Admin-UI ist
nur zur Pflege gedacht — der eigentliche Zugriff passiert über MCP/REST
aus Deinen Apps heraus.

---

## Die vier Dienste auf einen Blick

| Dienst | Port (intern) | Aufgabe | Image |
|---|---|---|---|
| `api` | 8000 (REST unter /api, MCP unter /mcp, React-Admin-UI als SPA) | Dein Python-Programm (FastAPI + React-Frontend + MCP) | Eigenes Build |
| `qdrant` | 6333 | Vector-Datenbank | `qdrant/qdrant:latest` |
| `ollama` | 11434 | Embedding-Modell + LLM | `ollama/ollama:latest` |
| `postgres` | 5432 | Metadaten, User, API-Keys | `postgres:16` |

Alle Dienste sind **nur intern erreichbar** (Docker-Netz `rag-net`),
von außen kommt man nur über einen vorgeschalteten Caddy — entweder
ein zentraler Edge-Caddy aus dem `julasim/Proxy`-Stack (Default,
empfohlen, siehe [Deployment-Modi](#deployment-modi)) oder ein
mitgelieferter eigener `rag-caddy` (Standalone-Override).

---

## Zwei harte Prinzipien

1. **Datensouveränität zuerst.** Default-LLM ist lokal (Ollama). OpenRouter
   ist optional und nur pro Projekt zuschaltbar.
2. **Einfach zu bedienen, sauber getrennt.** Ein `docker compose up -d`
   startet alles. Jeder Dienst bleibt aber in seinem eigenen Container.

---

## Die drei Ordnungsebenen

Ein Dokument wird immer durch diese drei unabhängigen Dimensionen beschrieben:

| Ebene | Zweck | Beispiel |
|---|---|---|
| **Projekt** (= Qdrant-Collection) | Harte Trennung + Zugriffsrechte | `unternehmen`, `privat`, `schule` |
| **Ordner** (`folder_path`, frei) | Hierarchische Ablage, entsteht dynamisch | `/Ausschreibungen/BVH_Musterstraße/` |
| **Tags** (TEXT[], frei) | Cross-Cutting-Labels für Quer-Suche | `["dringend", "2026-Q2", "ÖNORM"]` |

Keine Ordner-Templates. Keine Pflicht-Tags. Alles entsteht organisch
pro Projekt.

---

## Authentifizierung — zwei getrennte Welten

| Welt | Wer? | Wie? | Menge |
|---|---|---|---|
| **Admin-UI** | Mensch | E-Mail + Passwort (bcrypt) | 1–2 User |
| **MCP / REST** | Programme | API-Key (`rag_sk_…`), pro Projekt-Whitelist | beliebig viele |

API-Keys werden in der Admin-UI erstellt, **einmal** angezeigt,
danach nur noch als Hash gespeichert.

---

## Deployment-Modi

RAG OS unterstützt zwei Deployment-Modi. Wähle einen, je nachdem ob auf dem
Host parallel ein zentraler Edge-Proxy läuft oder nicht.

### Edge-Mode (Default, empfohlen)

`rag-api` hängt am externen Docker-Netzwerk `proxy`. Ein zentraler Edge-Caddy
(Repo [`julasim/Proxy`](https://github.com/julasim/Proxy), läuft unter
`/opt/Proxy/` auf der VPS) macht TLS-Terminierung + Domain-Routing für ALLE
App-Stacks auf der Maschine (KI_WIKI, Bau-OS, RAG_OS …).

```bash
docker compose up -d
```

Voraussetzungen:
- `julasim/Proxy`-Stack ist auf der VPS aktiv (legt das `proxy`-Docker-Netz an)
- Edge-Caddyfile hat einen Block für die RAG-Domain, der routet auf:
  - `rag-api:8000` für `/api/*`, `/mcp/*` **und** `/` (FastAPI serviert auch das
    React-Admin-Frontend als SPA) — es gibt nur noch **einen** Port (8000)

### Standalone-Mode

Bringt einen eigenen `rag-caddy` mit, der die Host-Ports 80/443 belegt.
Nutzen, wenn KEIN zentraler Edge-Caddy parallel läuft.

```bash
docker compose -f docker-compose.yml -f docker-compose.standalone.yml up -d
```

Der mitgelieferte `Caddyfile` im Repo-Root wird in diesem Modus aktiv.

---

## Quickstart (auf dem VPS)

```bash
# 1. Repo holen (oder via OneDrive-Sync auf den Server bringen)
cd /opt/rag

# 2. Secrets anlegen
cp .env.example .env
# → .env editieren: POSTGRES_PASSWORD, ADMIN_EMAIL, usw.

# 3. Starten — Edge-Mode (Default)
docker compose up -d
#    Für Standalone-Mode stattdessen:
#    docker compose -f docker-compose.yml -f docker-compose.standalone.yml up -d

# 4. Modelle laden (einmalig)
docker compose exec ollama ollama pull bge-m3
docker compose exec ollama ollama pull qwen2.5:3b-instruct

# 5. Admin-UI öffnen
# https://rag.deinedomain.at
```

---

## Projektstruktur

```
RAG_OS/
├── README.md                       ← Du bist hier
├── docker-compose.yml              ← Edge-Mode-Default (4 Services, hängt an externem proxy-Netz)
├── docker-compose.standalone.yml   ← Override: eigener rag-caddy (Ports 80/443)
├── docker-compose.localonly.yml    ← Override: lokales Dev ohne Domain
├── .env.example                    ← Secret-Template
├── Caddyfile                       ← HTTPS-Routing (nur im Standalone-Mode aktiv)
├── app/                            ← Dein Python-Programm
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── main.py
│   ├── api/                    ← REST-Endpunkte
│   ├── mcp/                    ← MCP-Server-Adapter
│   ├── ui/                     ← Streamlit-Admin
│   ├── pipelines/              ← Haystack-YAMLs
│   ├── db/                     ← Postgres-Models
│   ├── auth/                   ← API-Keys + UI-Login
│   └── ingest/                 ← Parser, Chunker, Folder-Watcher
├── config/
│   ├── projects.yml            ← Legacy-Seed (wird zur Migration aus alten Versionen genutzt)
│   └── project_defaults.yml    ← Defaults + Initial-Seed für die `projects`-Tabelle
├── docs/
│   └── ARCHITECTURE.md         ← Tiefer-Einstieg
└── data/                       ← (wird automatisch erzeugt)
    ├── qdrant/
    ├── postgres/
    ├── ollama/
    └── uploads/
```

---

## Evolution — Phasen

- **Phase 0** Infrastruktur (Compose hoch, Modelle geladen, Health-Checks grün)
- **Phase 1** Ingest PDF/DOCX, REST `/ingest` und `/query`
- **Phase 2** MCP-Server, erstes `rag_search` in Claude Desktop
- **Phase 3** Streamlit-Admin, API-Keys, Projekt-Trennung
- **Phase 4** OCR, weitere Parser, Reranker, OpenRouter-Fallback
- **Phase 5** Backups, Monitoring, zweiter UI-User

Fine-Tuning kommt **später** als separates Wochenendprojekt, falls RAG
allein nicht reicht.

---

## Lizenz

MIT für den Eigencode. Die verwendeten Bibliotheken bleiben unter ihren
jeweiligen Lizenzen (Haystack Apache 2.0, Qdrant Apache 2.0, Ollama MIT,
BGE-M3 MIT, Qwen 2.5 Apache 2.0).
