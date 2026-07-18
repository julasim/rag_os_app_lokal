#!/usr/bin/env bash
# =============================================================================
# SIMA RAG — Disaster-Recovery: Restore aus /data/backups
#
# Stellt Postgres (pg_restore) und die Qdrant-Collection (Snapshot-Upload)
# aus den letzten Backup-Dateien im Bind-Mount ./data/backups wieder her.
#
# Aufruf (aus dem Projektverzeichnis, Stack muss laufen):
#   ./scripts/restore.sh                 # nutzt jeweils neuestes Backup
#   ./scripts/restore.sh <pg.dump> <collection.snapshot>   # explizite Dateien
#
# Ausführlicher Kontext: docs/DISASTER-RECOVERY.md
# =============================================================================
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose}"
BACKUP_DIR="./data/backups"
COLLECTION="rag_documents"

log()  { echo -e "\033[1;34m[restore]\033[0m $*"; }
err()  { echo -e "\033[1;31m[restore]\033[0m $*" >&2; }

# .env laden (POSTGRES_*, QDRANT_API_KEY)
[ -f .env ] && set -a && . ./.env && set +a

PG_DUMP="${1:-$(ls -t "$BACKUP_DIR"/postgres_*.dump 2>/dev/null | head -1)}"
QD_SNAP="${2:-$(ls -t "$BACKUP_DIR"/*.snapshot 2>/dev/null | head -1)}"

[ -n "$PG_DUMP" ] && [ -f "$PG_DUMP" ] || { err "Kein Postgres-Dump gefunden ($BACKUP_DIR/postgres_*.dump)"; exit 1; }
[ -n "$QD_SNAP" ] && [ -f "$QD_SNAP" ] || { err "Kein Qdrant-Snapshot gefunden ($BACKUP_DIR/*.snapshot)"; exit 1; }

log "Postgres-Dump:    $(basename "$PG_DUMP")"
log "Qdrant-Snapshot:  $(basename "$QD_SNAP")"
read -r -p "Wiederherstellung überschreibt aktuelle Daten. Fortfahren? [ja/NEIN] " ok
[ "$ok" = "ja" ] || { err "Abgebrochen."; exit 1; }

# --- 1. Postgres -----------------------------------------------------------
log "Postgres wiederherstellen (pg_restore --clean --if-exists) …"
$COMPOSE cp "$PG_DUMP" postgres:/tmp/restore.dump
$COMPOSE exec -T postgres sh -c \
  'PGPASSWORD=$POSTGRES_PASSWORD pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists /tmp/restore.dump' \
  || log "pg_restore meldete Warnungen (bei --clean auf leerer DB normal)"
$COMPOSE exec -T postgres sh -c 'rm -f /tmp/restore.dump'

# --- 2. Qdrant-Collection --------------------------------------------------
log "Qdrant-Collection '$COLLECTION' aus Snapshot hochladen …"
# Snapshot in den qdrant-Container legen und per lokalem Upload wiederherstellen.
$COMPOSE cp "$QD_SNAP" qdrant:/qdrant/snapshots/restore.snapshot
$COMPOSE exec -T api python - <<'PY'
import httpx
from config import settings
s = settings()
h = {"api-key": s.qdrant_api_key} if s.qdrant_api_key else {}
url = f"{s.qdrant_url}/collections/rag_documents/snapshots/recover"
# priority=snapshot: Snapshot-Daten gewinnen gegenüber evtl. vorhandenen.
r = httpx.put(url, headers=h, json={
    "location": "file:///qdrant/snapshots/restore.snapshot",
    "priority": "snapshot",
}, timeout=600)
r.raise_for_status()
print("qdrant recover:", r.json())
PY

# --- 3. Verifikation -------------------------------------------------------
log "Verifikation:"
DOCS=$($COMPOSE exec -T postgres sh -c 'PGPASSWORD=$POSTGRES_PASSWORD psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM documents;"')
POINTS=$($COMPOSE exec -T api python -c "from pipelines.factory import get_vector_store; print(get_vector_store().count_documents())" 2>/dev/null | tail -1)
log "  Dokumente in Postgres: $DOCS"
log "  Punkte in Qdrant:      $POINTS"
log "Restore abgeschlossen."
