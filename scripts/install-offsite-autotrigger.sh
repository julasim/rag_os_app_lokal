#!/usr/bin/env bash
# =============================================================================
# Installiert den AUTO-TRIGGER für das Off-Site-Backup:
# Beim Anstecken einer USB-Platte läuft der Handler; wenn ihr Dateisystem-Label
# dem in scripts/offsite.conf entspricht, wird das Backup gespiegelt.
#
# Einmalig als root auf dem HOST ausführen:
#   sudo ./scripts/install-offsite-autotrigger.sh
#
# WICHTIG: Plattennamen/Optionen NICHT hier, sondern in scripts/offsite.conf
# pflegen — Änderungen dort wirken sofort, ohne Neuinstallation.
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen (sudo)."; exit 1; }

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$PROJECT_DIR/scripts/offsite.conf" ] \
  || { echo "scripts/offsite.conf fehlt in $PROJECT_DIR."; exit 1; }

# aktuelles Label nur zur Info anzeigen
# shellcheck disable=SC1090
. "$PROJECT_DIR/scripts/offsite.conf"
echo "Projektverzeichnis: $PROJECT_DIR"
echo "Aktuelles Label (aus offsite.conf): ${RAG_BACKUP_LABEL:-<leer>}"

# 1. systemd-Service (ruft den config-lesenden Handler; kein Label eingebacken)
cat > /etc/systemd/system/rag-offsite-backup.service <<EOF
[Unit]
Description=RAG OS Off-Site-Backup auf externe Platte
After=local-fs.target

[Service]
Type=oneshot
ExecStart=${PROJECT_DIR}/scripts/rag-offsite-handler.sh ${PROJECT_DIR}
EOF

# 2. Generische udev-Regel: JEDE USB-Dateisystem-Partition beim Anstecken
#    triggert den Service. Welche Platte tatsächlich gesichert wird, entscheidet
#    das Label in offsite.conf (der Handler prüft es). Dadurch ist ein
#    Umbenennen nur in offsite.conf nötig, nie hier.
cat > /etc/udev/rules.d/99-rag-offsite-backup.rules <<'EOF'
ACTION=="add", SUBSYSTEM=="block", ENV{ID_BUS}=="usb", ENV{ID_FS_USAGE}=="filesystem", TAG+="systemd", ENV{SYSTEMD_WANTS}="rag-offsite-backup.service"
EOF

systemctl daemon-reload
udevadm control --reload-rules

echo
echo "Installiert."
echo "  Platte mit dem Label aus offsite.conf anstecken → Backup läuft automatisch."
echo "  Label ändern:        nano ${PROJECT_DIR}/scripts/offsite.conf   (kein Reinstall nötig)"
echo "  Manuell auslösen:    ${PROJECT_DIR}/scripts/offsite-backup-now.sh"
echo "  Log ansehen:         journalctl -u rag-offsite-backup.service -n 40 --no-pager"
