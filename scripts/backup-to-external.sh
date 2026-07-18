#!/usr/bin/env bash
# =============================================================================
# SIMA RAG — Off-Site-Backup auf externe Festplatte
#
# Spiegelt die lokalen Backups (./data/backups: pg_dump + Qdrant-Snapshots) auf
# eine an den Host angesteckte externe Platte. Läuft auf dem HOST (nicht im
# Container), weil die externe Platte am Host hängt.
#
# Aufruf (aus dem Projektverzeichnis /opt/<projekt>):
#   ./scripts/backup-to-external.sh /mnt/backup-hdd
#   RAG_BACKUP_EXTERNAL=/mnt/backup-hdd ./scripts/backup-to-external.sh
#
# Optionale Umgebungsvariablen:
#   RAG_BACKUP_EXTERNAL        Ziel-Mountpoint der externen Platte (statt Arg 1)
#   RAG_BACKUP_SRC             Quelle (Default: ./data/backups)
#   RAG_BACKUP_SUBDIR          Unterordner auf der Platte (Default: rag-os-backups)
#   RAG_BACKUP_EXTERNAL_KEEP_DAYS  Aufbewahrung auf der Platte (Default: 90, 0=nie löschen)
#   RAG_BACKUP_GPG_RECIPIENT  GPG-Empfänger → Backups verschlüsselt ablegen
#                             (empfohlen! Dumps enthalten Passwort-/Key-Hashes)
# =============================================================================
set -euo pipefail

SRC="${RAG_BACKUP_SRC:-./data/backups}"
DEST_ROOT="${1:-${RAG_BACKUP_EXTERNAL:-}}"
SUBDIR="${RAG_BACKUP_SUBDIR:-rag-os-backups}"
KEEP_DAYS="${RAG_BACKUP_EXTERNAL_KEEP_DAYS:-90}"
GPG_RECIPIENT="${RAG_BACKUP_GPG_RECIPIENT:-}"

log() { echo "[offsite] $*"; }
err() { echo "[offsite] FEHLER: $*" >&2; exit 1; }

# --- Vorprüfungen -----------------------------------------------------------
[ -n "$DEST_ROOT" ] || err "Kein Ziel. Aufruf: $0 /mnt/backup-hdd  (oder RAG_BACKUP_EXTERNAL setzen)"
[ -d "$SRC" ] || err "Backup-Quelle fehlt: $SRC — aus dem Projektverzeichnis ausführen?"

# Ziel MUSS ein echter Mountpoint sein — sonst schriebe man aus Versehen auf die
# Systemplatte (kein Off-Site) oder ins Leere, wenn die Platte nicht angesteckt ist.
mountpoint -q "$DEST_ROOT" || err "$DEST_ROOT ist kein Mountpoint. Externe Platte angesteckt und gemountet?"

DEST="$DEST_ROOT/$SUBDIR"
mkdir -p "$DEST" || err "Kann $DEST nicht anlegen (Schreibrechte? read-only gemountet?)"

command -v rsync >/dev/null 2>&1 || err "rsync nicht installiert (sudo apt-get install -y rsync)"
if [ -n "$GPG_RECIPIENT" ]; then
  command -v gpg >/dev/null 2>&1 || err "gpg nicht installiert (sudo apt-get install -y gnupg)"
fi

# --- Backup-Dateien einsammeln ---------------------------------------------
shopt -s nullglob
files=("$SRC"/postgres_*.dump "$SRC"/*.snapshot)
[ "${#files[@]}" -gt 0 ] || err "Keine Backup-Dateien in $SRC — läuft der nächtliche Backup?"

# --- Kopieren (nur Neues; verifiziert) -------------------------------------
copied=0
for f in "${files[@]}"; do
  base=$(basename "$f")
  if [ -n "$GPG_RECIPIENT" ]; then
    out="$DEST/$base.gpg"
    [ -f "$out" ] && continue
    tmp="$out.part"
    gpg --yes --batch --encrypt --recipient "$GPG_RECIPIENT" --output "$tmp" "$f" \
      || err "GPG-Verschlüsselung fehlgeschlagen: $base"
    mv "$tmp" "$out"
  else
    out="$DEST/$base"
    [ -f "$out" ] && continue
    # rsync mit Checksummen-Verifikation, atomar (temp + rename via rsync)
    rsync -a --checksum "$f" "$out" || err "Kopie fehlgeschlagen: $base"
  fi
  copied=$((copied + 1))
  log "kopiert: $(basename "$out")"
done

sync   # sicherstellen, dass alles physisch auf der Platte ist
log "$copied neue Datei(en) → $DEST"

# --- Retention auf der externen Platte (länger als lokal) ------------------
if [ "$KEEP_DAYS" -gt 0 ]; then
  while IFS= read -r d; do
    log "alt gelöscht: $(basename "$d")"
  done < <(find "$DEST" -type f \
             \( -name 'postgres_*' -o -name '*.snapshot' -o -name '*.snapshot.gpg' \) \
             -mtime +"$KEEP_DAYS" -print -delete)
fi

# --- Bestand melden ---------------------------------------------------------
total=$(find "$DEST" -type f | wc -l)
size=$(du -sh "$DEST" 2>/dev/null | cut -f1)
log "Bestand auf externer Platte: $total Datei(en), $size"
log "Fertig. Platte sicher aushängen:  sync && sudo umount $DEST_ROOT"
