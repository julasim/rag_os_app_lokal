# Deployment-Guide

Schritt-für-Schritt-Anleitung, um das RAG-System auf einem Hetzner-VPS
in Betrieb zu nehmen. Zeitaufwand beim ersten Mal: **~45 Minuten**.

---

## 0. Voraussetzungen (bitte vorher erledigen)

| Was | Empfehlung |
|---|---|
| **VPS** | Hetzner CX32 (8 GB RAM, 4 vCPU, 80 GB SSD), Ubuntu 24.04 LTS |
| **Domain** | z. B. `rag.sima.or.at` — A-Record auf die VPS-IP |
| **SSH-Zugang** | mit SSH-Key, als `root` oder sudo-fähiger User |
| **Code** | dieses Projekt-Verzeichnis |

> **Warum CX32?** 1.5 GB Qdrant + 1.5 GB Python-App + 5 GB Ollama (7B-Modell, Q4) + 0.5 GB Postgres = ca. 8.5 GB → CX32 ist die sinnvolle Untergrenze. Mit 4 GB Swap (macht `bootstrap-vps.sh` automatisch) auch unter Last stabil.
>
> **Seit Juli 2026 (Reranker):** Der Post-Retrieval-Reranker (`bge-reranker-v2-m3`,
> ~2.4 GB) wird beim ersten `rag_retrieve` in den RAM geladen. Das
> api-Container-Limit steht deshalb in `docker-compose.yml` auf **4 GB**
> (`deploy.resources.limits.memory`). Auf 8-GB-VPS mit Ollama gleichzeitig
> kann es eng werden — der 4-GB-Swap fängt das ab. Wenn RAM knapp ist:
> `RERANK_ENABLED=false` in `.env` setzen (Hybrid + Metadaten bleiben, nur die
> Präzision ist etwas geringer).

---

## 1. VPS vorbereiten (einmalig)

Sobald Du via SSH drauf bist:

```bash
# Bootstrap holen + ausführen
wget https://raw.githubusercontent.com/<DEIN_REPO>/main/scripts/bootstrap-vps.sh
# oder per scp hochladen, falls kein Git-Repo verfügbar
chmod +x bootstrap-vps.sh
sudo ./bootstrap-vps.sh

# Wichtig: neu einloggen (damit Docker-Gruppe greift)
exit
ssh julius@<VPS-IP>
```

Das Skript installiert:
- Docker + Docker Compose
- Firewall (UFW: SSH, HTTP, HTTPS)
- Docker-Log-Rotation (100 MB × 3)
- 4 GB Swap-File

**Test:** `docker ps` sollte ohne `sudo` funktionieren.

---

## 2. Code auf den VPS bringen

Drei Optionen, wähle eine:

### Option A — Git (empfohlen für Versionierung)

```bash
sudo mkdir -p /opt/rag && sudo chown $USER /opt/rag
cd /opt/rag
git clone https://github.com/<DEIN_REPO>.git .
```

### Option B — rsync von lokal (Windows → Linux via WSL oder Git-Bash)

Von Deinem Rechner, im Ordner `3_Unternehmen/KI-OS/RAG System/`:

```bash
rsync -avz --delete \
  --exclude '.env' --exclude 'data/' --exclude '__pycache__' --exclude '.venv' \
  ./ julius@<VPS-IP>:/opt/rag/
```

### Option C — SFTP mit WinSCP / FileZilla

Lade den gesamten Projekt-Ordner nach `/opt/rag/` auf dem VPS hoch.
Exkludiere: `data/`, `__pycache__/`, `.venv/`, **eine evtl. lokale `.env`**.

---

## 3. Secrets + Konfiguration

Auf dem VPS, im Ordner `/opt/rag`:

```bash
cp .env.example .env
bash scripts/generate-secrets.sh >> .env    # fügt starke Zufallswerte an
nano .env
```

Was Du **manuell** einstellen musst (nicht auto-generierbar):

| Variable | Beispielwert | Erklärung |
|---|---|---|
| `RAG_DOMAIN` | `rag.sima.or.at` | Deine Domain |
| `ADMIN_EMAIL` | `julius@sima.or.at` | Login-E-Mail der Admin-UI |

Das Generator-Skript ergänzt `APP_SECRET_KEY`, `POSTGRES_PASSWORD`, `QDRANT_API_KEY`, `ADMIN_PASSWORD` als **doppelte** Einträge unten in der Datei — lösche die Platzhalter oben. Am Ende hat jede Variable nur einen Wert.

> **Das initiale `ADMIN_PASSWORD` aus dem Generator notieren** — damit loggst Du Dich das erste Mal ein.

---

## 4. Erst-Start

```bash
cd /opt/rag
docker compose up -d --build
```

Das dauert beim ersten Mal **~5-10 Minuten** (Images werden gezogen, Python-Deps installiert).

Live-Verlauf ansehen:

```bash
make logs
# oder: docker compose logs -f
```

Warte, bis die API-Logs ruhig sind und Du siehst:
```
[app.boot] ...
[db.init.done] ...
[qdrant.collection_ready] project=privat
[qdrant.collection_ready] project=schule
[qdrant.collection_ready] project=unternehmen
[mcp.session_manager.started] ...
```

---

## 5. Modelle ziehen (einmalig, ~6 GB Download)

```bash
make models
```

Das pullt:
- `bge-m3` (~1.2 GB) — Embedding-Modell
- `qwen2.5:3b-instruct` (~2 GB) — LLM

Prüfung:
```bash
make models-status
```

---

## 6. Health-Check

```bash
make health
```

Erwartete Antwort:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "services": { "postgres": true, "qdrant": true, "ollama": true }
}
```

Wenn einer auf `false` steht → `make logs-api` und den Fehler nachlesen.

---

## 7. Erster Login in die Admin-UI

1. Browser öffnen: `https://rag.deinedomain.at`
2. Login: `ADMIN_EMAIL` + das `ADMIN_PASSWORD`, das Du oben notiert hast.
3. Du landest im Dashboard. Alle drei Projekte sollten 0 Dokumente zeigen.

---

## 8. Ersten API-Key erstellen

In der UI → **API-Keys** → **Neuen Key erstellen**:

- Label: `claude_desktop_privat`
- Erlaubte Projekte: `privat`
- Rechte: `read`, `write`

Der Klartext-Key wird **einmal** angezeigt — kopieren + sicher verwahren.

Test mit `curl`:
```bash
curl -H "Authorization: Bearer rag_sk_..." \
     https://rag.deinedomain.at/api/projects
```

---

## 9. Als MCP in Claude Desktop einbinden

In Claude Desktops `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sima-rag": {
      "url": "https://rag.deinedomain.at/mcp",
      "headers": {
        "Authorization": "Bearer rag_sk_..."
      }
    }
  }
}
```

Claude Desktop neu starten. Dann sollten in der Tool-Liste `rag_search`, `rag_list_documents`, `rag_list_projects` usw. auftauchen.

---

## Troubleshooting

### `docker compose up` schlägt fehl mit "Cannot connect to the Docker daemon"
→ Du bist nicht in der `docker`-Gruppe. `exit` + neu einloggen.

### Caddy-Log sagt "could not obtain TLS certificate"
→ Die Domain zeigt noch nicht auf den VPS. Prüfe A-Record mit `dig rag.sima.or.at`. Sobald DNS stimmt, holt Caddy automatisch ein neues Zertifikat (kann bis zu 5 Min dauern).

### API-Logs zeigen `ImportError`
→ Image war alt. `make rebuild && make up`.

### Dashboard zeigt Qdrant = false
→ Qdrant-Container läuft nicht oder `QDRANT_API_KEY` stimmt nicht zwischen Qdrant und API. Prüfe: `docker compose logs qdrant`.

### Antworten fühlen sich "unspezifisch" an
→ Hast Du Dokumente hochgeladen? Leere Collection = Early-Exit mit *"nicht in der Sammlung"*. Lade in der UI ein PDF hoch, warte auf Status = `indexed`, dann nochmal fragen.

### LLM ist sehr langsam (>30 Sek pro Antwort)
→ Normal auf reinem CPU-Inference (7B-Modell, 4 vCPU). Optionen: kleineres Modell (`qwen2.5:3b-instruct`), oder OpenRouter für das betroffene Projekt aktivieren.

---

## Updates einspielen (Workflow)

**Mit Git:**
```bash
cd /opt/rag
git pull
make rebuild        # nur wenn Python-Code oder Dockerfile geändert
make restart        # nur wenn Compose/Env geändert
```

**Mit rsync (lokal):**
```bash
rsync -avz --delete --exclude '.env' --exclude 'data/' \
  ./ julius@<VPS-IP>:/opt/rag/
ssh julius@<VPS-IP> 'cd /opt/rag && make rebuild'
```

### Migrations-Sonderfälle (nicht automatisch!)

Manche Releases brauchen einen einmaligen Zusatzschritt **nach** `make rebuild`:

**Hybrid-Suche (Release Juli 2026, dense + BM25-sparse):** Die Qdrant-Collection
bekommt erstmals einen Sparse-Vektor. Eine bestehende reine-dense-Collection
muss dafür **neu angelegt und alle Dokumente neu eingebettet** werden — sonst
greift die Hybrid-Suche nicht (und die Suche kann bis dahin Fehler werfen).

```bash
cd /opt/rag
make backup                                 # vorher! reset löscht die Collection
git pull && make rebuild

# Re-Index direkt im Container (robust: kein Proxy-Timeout, keine Auth/TLS nötig)
docker compose exec -T api python -c "import asyncio; from ingest.pipeline import reindex_all; print(asyncio.run(reindex_all(reset=True)))"
# Erwartet: {'total': N, 'reindexed': N, 'skipped': 0, 'failed': 0}

make logs-api      # collection_recreated + reindex_all.done + reranker.ready
```

Hinweise:
- **Kurze Such-Downtime** während des Re-Index (Collection wird gedroppt + neu befüllt).
- **Erster `rag_retrieve` lädt Modelle** (Reranker ~2.4 GB, BM25) einmalig aus dem
  Netz → Container braucht Internet + Platz. `make models` ist davon unabhängig
  (nur Ollama-Modelle bge-m3/qwen).
- **Frischer Deploy ohne Dokumente:** Re-Index entfällt — die Collection wird
  beim ersten Upload gleich mit Sparse-Vektor angelegt.
- Endpoint-Variante (falls bevorzugt): `POST /api/reindex-all?reset=true` mit
  Admin-Auth (`X-UI-Token` aus `/api/auth/login`) — bei großem Bestand droht
  aber ein Proxy-Timeout, daher oben der Container-Weg.

### Stolpersteine beim Update (real erlebt, 2026-07-13)

**502 nach `--force-recreate` / `make restart` von `rag-api`.** Beim Recreate
bekommt der Container eine **neue IP** im `proxy`-Netz; der Edge-Caddy hält aber
oft noch die alte gecacht → `502 Bad Gateway`, obwohl die App intern gesund ist
(`docker compose exec -T api curl -s localhost:8000/api/health` → ok). **Fix:**
den Edge-Caddy einmal neu auflösen lassen:
```bash
docker exec edge-caddy caddy reload --config /etc/caddy/Caddyfile
# Caddy-Name unklar?  docker ps --format '{{.Names}}' | grep -i caddy
# hilft der reload nicht:  cd /opt/Proxy && docker compose restart
```
Deshalb `--force-recreate`/`make restart` von `rag-api` nur wenn nötig (z.B.
`.env`-Änderung) — und danach **immer** an den Caddy-Reload denken. (Sauberer
Dauer-Fix wäre dynamische Upstream-Auflösung im Edge-Caddy — gehört ins
`julasim/Proxy`-Repo, nicht hierher.)

**`RAG_DOMAIN` muss exakt stimmen.** Der Wert in `.env` muss die Domain sein, die
(a) per DNS-A-Record auf den VPS zeigt UND (b) einen Caddy-Block im Edge-Proxy
hat. Ein Tippfehler/falsche TLD → `make health` scheitert (`Could not resolve
host`) und CORS erlaubt die falsche Origin. Prod-Wert dieses Deployments:
`rag-os.sima.business` (ohne `.at`). Nach Korrektur: `docker compose up -d
--force-recreate api` + Caddy-Reload (siehe oben).

---

## Backups (empfohlen: täglich via cron)

Manuell:
```bash
make backup
```

Automatisch via cron (als `julius`):
```bash
crontab -e
# tägliches Backup um 03:00
0 3 * * * cd /opt/rag && /usr/bin/make backup >> /var/log/rag-backup.log 2>&1
```

Nach Hetzner Storage Box auslagern:
```bash
rsync -avz /opt/rag/backups/ u123456@u123456.your-storagebox.de:rag-backups/
```

---

## MCP-Connector anhängen (Claude.ai, Claude Code, …)

RAG OS ist ein MCP-Server unter `https://<RAG_DOMAIN>/mcp`. Zwei Auth-Wege:

**Claude.ai / Claude Desktop „Custom Connector" — OAuth (default AN):**
Einfach die MCP-URL als Connector hinzufügen:
```
https://rag-os.sima.business/mcp
```
Claude.ai entdeckt den Auth-Server über die `.well-known`-Endpunkte, öffnet den
Browser zur Anmeldung → **mit den Admin-UI-Zugangsdaten einloggen**
(`ADMIN_EMAIL`/`ADMIN_PASSWORD`). Kein manuelles Token nötig. OAuth ist ohne
Extra-Config aktiv (JWT-Secret = `APP_SECRET_KEY`; abschaltbar via
`OAUTH_ENABLED=false`). Clients/Refresh-Tokens liegen in Postgres (überleben
Redeploys, im pg_dump).

**Claude Code (CLI) / eigene Clients — statischer API-Key (einfacher):**
In der Admin-UI unter **API-Keys** einen Key (Scope `read`) erstellen, dann:
```bash
claude mcp add --transport http rag-os https://rag-os.sima.business/mcp \
  --header "Authorization: Bearer rag_sk_DEIN_KEY"
```

Beide Wege laufen parallel. Wichtig: `RAG_DOMAIN` muss die echte Domain sein
(OAuth-`iss`/`aud` werden daraus gebaut) — siehe Stolpersteine oben.

---

## Entsorgung / Zurücksetzen

```bash
# Alles stoppen und Volumes löschen (KEIN Backup!)
docker compose down -v
sudo rm -rf data/ backups/
```
