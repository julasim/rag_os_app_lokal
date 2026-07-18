#!/usr/bin/env bash
# =============================================================================
# Off-Site-Backup-Handler — findet die externe Platte per LABEL (aus
# scripts/offsite.conf), mountet sie temporär, spiegelt die Backups
# (backup-to-external.sh) und hängt sie wieder aus.
#
# Wird aufgerufen von:
#   - offsite-backup-now.sh  (manuell)
#   - dem systemd-Service    (automatisch beim Anstecken)
#
# Aufruf:  rag-offsite-handler.sh [PROJEKT-DIR]
# Konfiguration ausschließlich über scripts/offsite.conf — Label hier NICHT
# als Argument, damit ein Umbenennen nur an einer Stelle passiert.
# =============================================================================
set -euo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
CONF="$PROJECT_DIR/scripts/offsite.conf"

log() { echo "[offsite-handler] $*"; }

[ -f "$CONF" ] || { log "Config fehlt: $CONF"; exit 1; }
# shellcheck disable=SC1090
. "$CONF"

LABEL="${RAG_BACKUP_LABEL:-}"
[ -n "$LABEL" ] || { log "RAG_BACKUP_LABEL nicht gesetzt in $CONF"; exit 1; }

# Platte per Label suchen (kurz warten/retryen — beim Anstecken braucht udev
# einen Moment, bis das Label bekannt ist).
DEV=""
for _ in 1 2 3 4 5; do
  DEV="$(blkid -L "$LABEL" 2>/dev/null || true)"
  [ -n "$DEV" ] && break
  sleep 1
done
if [ -z "$DEV" ]; then
  log "Keine Platte mit Label '$LABEL' gefunden — nichts zu tun."
  exit 0
fi
log "Platte gefunden: $DEV (Label $LABEL)"

MNT="/run/rag-offsite-${LABEL}"
mkdir -p "$MNT"
_we_mounted=0
if ! mountpoint -q "$MNT"; then
  mount "$DEV" "$MNT"
  _we_mounted=1
fi
cleanup() {
  sync
  if [ "$_we_mounted" = "1" ]; then
    umount "$MNT" 2>/dev/null || log "Warnung: umount $MNT fehlgeschlagen — später manuell aushängen."
  fi
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

cd "$PROJECT_DIR" || { log "Projektverzeichnis $PROJECT_DIR nicht gefunden"; exit 1; }
# Optionen aus der Config an den Sync durchreichen.
RAG_BACKUP_EXTERNAL="$MNT" \
RAG_BACKUP_GPG_RECIPIENT="${RAG_BACKUP_GPG_RECIPIENT:-}" \
RAG_BACKUP_EXTERNAL_KEEP_DAYS="${RAG_BACKUP_EXTERNAL_KEEP_DAYS:-90}" \
  ./scripts/backup-to-external.sh
log "Off-Site-Backup abgeschlossen — Platte kann abgezogen werden."
