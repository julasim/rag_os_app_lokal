# Architektur-Dokument

> ⚠️ **In Teilen VERALTET.** Quelle der Wahrheit ist **`CLAUDE.md`** (Goldener Pfad §2,
> Datenmodell §4, MCP-only §5). Insbesondere überholt in diesem Dokument:
> **kein „Projekt"-Konzept / keine Collection-pro-Projekt** (nur Ordner + Tags, EINE
> globale Collection `rag_documents`), **kein `/api/query`** (Suche ist MCP-only über
> `rag_retrieve`), **keine Streamlit-UI**, **kein OpenRouter/Cloud-LLM** (100 % lokal).
> Beim Lesen gegen den tatsächlichen Code prüfen.

Diese Datei erklärt das System **aus Entwicklersicht** — als Nachschlagewerk
für Dich selbst, wenn Du in sechs Monaten nicht mehr weißt, warum etwas
so und nicht anders gebaut ist.

---

## 1. Der Datenfluss

### Ingest (Dokument → Qdrant)

```
Datei
  │
  ▼
[1] Upload (REST /api/ingest  oder  Folder-Watch)
  │
  ▼
[2] SHA256-Hash berechnen             → Duplikat-Erkennung
  │
  ▼
[3] Parser nach MIME-Typ              → PyMuPDF, python-docx, openpyxl, …
  │
  ▼
[4] Text + Struktur extrahieren       → Überschriften, Seitenzahlen
  │
  ▼
[5] Semantisches Chunking             → 700 Tokens, 80 Overlap
  │                                     (kein Satz-Bruch)
  ▼
[6] Metadata anreichern               → project, folder_path, tags,
  │                                     file_name, page, upload_date, hash
  ▼
[7] Embedding via Ollama (BGE-M3)     → Dense + Sparse
  │
  ▼
[8] Upsert in Qdrant (Collection=Projekt)
  │
  ▼
[9] Eintrag in Postgres                → status="indexed", chunk_count
```

### Query (Frage → Antwort)

```
Client-Request (MCP oder REST)
  │
  ▼
[1] Auth — API-Key prüfen             → welches Projekt? welche Rechte?
  │
  ▼
[2] Query-Embedding (BGE-M3)
  │
  ▼
[3] Hybrid-Search in Qdrant           → top_k=20, gefiltert auf
  │                                     project + optional folder
  ▼
[4] Reranking                         → Top 5 an LLM
  │
  ▼
[5] Prompt bauen                      → Template + Chunks + Frage
  │
  ▼
[6] LLM-Call (Ollama oder OpenRouter) → laut Projekt-Config
  │
  ▼
[7] Antwort + strukturierte Quellen   → file, page, score
  │
  ▼
Response (JSON)
```

---

## 2. Die drei Ordnungsebenen

Ein Dokument wird **immer** durch genau diese drei unabhängigen Dimensionen
beschrieben. Sie sind orthogonal — jede kann unabhängig von der anderen
gefiltert werden.

### Projekt (Collection)
- **Zweck:** Harte Trennung + Zugriffskontrolle
- **Technisch:** Eine Qdrant-Collection pro Projekt
- **Leben:** Wird in `config/projects.yml` definiert, stabil
- **Zugriff:** API-Keys haben eine Projekt-Whitelist
- **Beispiele:** `privat`, `schule`, `unternehmen`

### Ordner (`folder_path`)
- **Zweck:** Hierarchische Ablage wie ein Dateisystem
- **Technisch:** Freies TEXT-Feld in Postgres + Qdrant-Payload
- **Leben:** Entsteht beim Upload, keine Vorlage, beliebig nestbar
- **Zugriff:** Optional als Filter bei Queries nutzbar
- **Beispiele:** `/`, `/Ausschreibungen/BVH_Musterstraße/`, `/Steuer/2025/`

### Tags
- **Zweck:** Cross-Cutting-Labels für Quer-Suche
- **Technisch:** `TEXT[]` in Postgres + Qdrant-Payload
- **Leben:** frei, mehrere pro Dokument
- **Zugriff:** Filter: *"alle Dokumente mit Tag `dringend`"*
- **Beispiele:** `["dringend", "2026-Q2", "ÖNORM"]`

---

## 3. Datenbank-Schemas

### Qdrant (eine Collection pro Projekt)

```
Collection: projekt_{name}
  Vector-Config:
    - size: 1024 (BGE-M3)
    - distance: Cosine
    - hybrid: true (Dense + Sparse)

  Point:
    id: UUID
    vector: [float] × 1024
    sparse_vector: {token_id: weight}
    payload:
      doc_id: str           (sha256 des Originaldokuments)
      chunk_index: int
      text: str
      file_name: str
      folder_path: str      ("/" wenn Root)
      page: int | null
      section_title: str | null
      source_type: str      ("pdf" | "docx" | …)
      tags: [str]
      created_at: datetime
```

### Postgres

```sql
-- Benutzer der Admin-UI
CREATE TABLE ui_users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','viewer')),
    created_at    TIMESTAMPTZ DEFAULT now(),
    last_login    TIMESTAMPTZ
);

-- API-Keys für MCP/REST-Clients
CREATE TABLE api_keys (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash          TEXT UNIQUE NOT NULL,            -- bcrypt
    label             TEXT NOT NULL,
    allowed_projects  TEXT[] NOT NULL,
    scopes            TEXT[] NOT NULL,                 -- read | write | delete
    created_at        TIMESTAMPTZ DEFAULT now(),
    last_used_at      TIMESTAMPTZ,
    expires_at        TIMESTAMPTZ,
    created_by        UUID REFERENCES ui_users(id)
);

-- Dokument-Metadaten (Single Source of Truth für "was haben wir?")
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_hash      TEXT NOT NULL,                       -- SHA256
    project       TEXT NOT NULL,
    folder_path   TEXT NOT NULL DEFAULT '/',
    file_name     TEXT NOT NULL,
    file_path     TEXT NOT NULL,                       -- abs. Pfad auf Disk
    mime_type     TEXT,
    size_bytes    BIGINT,
    tags          TEXT[] DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'queued',      -- queued|processing|indexed|failed
    chunk_count   INT DEFAULT 0,
    error_msg     TEXT,
    uploaded_at   TIMESTAMPTZ DEFAULT now(),
    indexed_at    TIMESTAMPTZ,
    uploaded_by   UUID REFERENCES ui_users(id),
    UNIQUE (project, doc_hash)
);

CREATE INDEX idx_documents_project       ON documents(project);
CREATE INDEX idx_documents_folder        ON documents(project, folder_path);
CREATE INDEX idx_documents_status        ON documents(status);
CREATE INDEX idx_documents_tags          ON documents USING GIN(tags);

-- Log der Ingestion-Jobs (für Debug + Monitoring)
CREATE TABLE ingest_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id      UUID REFERENCES documents(id) ON DELETE CASCADE,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INT,
    status      TEXT NOT NULL,
    error_msg   TEXT
);

-- Log der Queries (für Analyse + Billing/Limits)
CREATE TABLE query_log (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id        UUID REFERENCES api_keys(id),
    project           TEXT NOT NULL,
    query_text        TEXT NOT NULL,
    retrieved_doc_ids UUID[],
    latency_ms        INT,
    model             TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);
```

---

## 4. Schnittstellen

### REST-API (intern für Admin-UI, extern für eigene Programme)

```
Auth (Admin):
  POST   /api/auth/login
  POST   /api/auth/logout
  GET    /api/auth/me

Documents:
  POST   /api/documents              (Multipart: file, project, folder_path?, tags?)
  GET    /api/documents?project=x&folder=/Steuer/
  GET    /api/documents/{id}
  DELETE /api/documents/{id}
  POST   /api/documents/{id}/reindex
  PATCH  /api/documents/{id}         (Tags, Folder ändern)

Query:
  POST   /api/query
    Body: {project, query, folder?, top_k?, llm_override?}

API-Keys (nur Admin):
  GET    /api/keys
  POST   /api/keys
  DELETE /api/keys/{id}

Projects:
  GET    /api/projects
  GET    /api/projects/{name}/stats

System:
  GET    /api/health
  GET    /api/stats
```

### MCP-Tools (für Agent-Clients)

```
rag_search(query, project, folder?, top_k?=5)
    → {answer, sources[], latency_ms}

rag_list_documents(project, folder?)
    → [{id, file_name, folder_path, tags, status, indexed_at}]

rag_get_document(doc_id)
    → {metadata, chunks[]}

rag_upload(file_path, project, folder?, tags?)
    → {doc_id, status}

rag_delete_document(doc_id)
    → {success}

rag_list_projects()
    → [{name, label, description, doc_count}]

rag_stats(project?)
    → {document_count, chunk_count, size_mb, last_indexed}
```

Jedes MCP-Tool prüft zuerst den API-Key aus dem Request-Header und die
darin eingetragene Projekt-Whitelist.

---

## 5. Sicherheits-Modell

| Angriffs­vektor | Schutz |
|---|---|
| Öffentliches Internet | Caddy + Let's Encrypt (HTTPS) |
| API-Key kompromittiert | Nur Zugriff auf die Projekte in der Whitelist, Key widerrufbar |
| Admin-Passwort raten | bcrypt, Rate-Limit auf Login-Endpunkt |
| Schwacher `APP_SECRET_KEY` | Muss 48+ Byte sein, wird beim Startup geprüft |
| Interne Dienste (Qdrant, Postgres) | `expose` statt `ports`, nur im Docker-Netz |
| Datei-Upload mit Malware | Typ-Whitelist, Größenlimit, (später ClamAV) |
| SQL-Injection | SQLAlchemy mit Parametern, nie String-Concat |
| XSS in UI | Streamlit escapet automatisch |

---

## 6. Phasen-Plan

| Phase | Was läuft? | Fertig, wenn… |
|---|---|---|
| **0** | Compose hoch, Health-Checks grün, Modelle geladen | `curl https://rag.domain.at/api/health` → 200 |
| **1** | Ingest PDF/DOCX + Query (nur REST, kein UI) | `POST /api/ingest` und `POST /api/query` funktionieren |
| **2** | MCP-Server via hayhooks, `rag_search` in Claude Desktop | Claude Desktop kann `rag_search` aufrufen |
| **3** | Streamlit-Admin, API-Keys, Projekt-Trennung, Folder-Baum | Zwei User können sich einloggen, Dateien managen |
| **4** | OCR (Tesseract), weitere Parser, Reranker, OpenRouter | Gescannte PDFs werden lesbar, OpenRouter optional |
| **5** | Backups (Qdrant-Snapshot + pg_dump), Basic Monitoring | Nächtliches Backup in Hetzner Storage Box |

---

## 7. Entscheidungen, die bewusst NICHT getroffen wurden

| Nicht gemacht | Warum |
|---|---|
| Separater Worker-Container | Am Anfang reicht asyncio im API-Prozess |
| Prometheus/Grafana | Docker-Logs reichen für Phase 0-3 |
| TEI statt Ollama für Embeddings | 1 Dienst weniger; BGE-M3 läuft in Ollama fine |
| Rate-Limiting | Single-User-System, kommt wenn nötig |
| SSO/OAuth | 2 User, bcrypt genügt |
| Kubernetes | Hobbyprojekt, Compose ist genug |
| Fine-Tuning-Pipeline | Separates Projekt, wenn RAG nicht ausreicht |
| Folder-Vorlagen pro Projekt | Alles dynamisch, jedes Projekt ist verschieden |
