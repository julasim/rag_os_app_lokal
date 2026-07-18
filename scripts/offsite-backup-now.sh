#!/usr/bin/env bash
# =============================================================================
# Off-Site-Backup JETZT manuell auslösen.
#
# Externe Platte (Label aus scripts/offsite.conf) anstecken, dann:
#   ./scripts/offsite-backup-now.sh
#
# Findet die Platte per Label, mountet sie, spiegelt die Backups und hängt sie
# wieder aus. Braucht root (zum Mounten) — ruft sich bei Bedarf per sudo neu auf.
# =============================================================================
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  exec sudo "$DIR/scripts/rag-offsite-handler.sh" "$DIR"
fi
exec "$DIR/scripts/rag-offsite-handler.sh" "$DIR"
