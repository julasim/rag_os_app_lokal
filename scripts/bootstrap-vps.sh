#!/usr/bin/env bash
# =============================================================================
# SIMA RAG — VPS-Bootstrap
#
# Bereitet einen frischen Ubuntu-22.04/24.04-VPS für den RAG-Stack vor:
#   - System-Updates
#   - Docker + Docker-Compose (offizielle Repos)
#   - Firewall (UFW) auf 22/80/443
#   - Basic-Tools: make, jq, curl, git, unzip
#   - Optional: Docker-Log-Rotation
#
# Verwendung (auf dem VPS, als root oder mit sudo):
#   curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/scripts/bootstrap-vps.sh | sudo bash
# oder lokal:
#   sudo ./scripts/bootstrap-vps.sh
# =============================================================================

set -euo pipefail

log()  { echo -e "\033[1;34m[bootstrap]\033[0m $*"; }
warn() { echo -e "\033[1;33m[bootstrap]\033[0m $*"; }
err()  { echo -e "\033[1;31m[bootstrap]\033[0m $*" >&2; }

# --- Sanity-Check -----------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
  err "Bitte als root oder mit sudo ausführen."
  exit 1
fi

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
  warn "Dieses Skript ist für Ubuntu getestet (erkannt: $ID). Fahre trotzdem fort."
fi

# --- System aktualisieren ---------------------------------------------------
log "System-Updates"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

# --- Basistools -------------------------------------------------------------
log "Installiere Basis-Tools"
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release \
  git make jq unzip ufw htop

# --- Docker (offizielles Repo) ---------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installiere Docker Engine"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io \
                     docker-buildx-plugin docker-compose-plugin
else
  log "Docker bereits installiert — überspringe"
fi

systemctl enable --now docker

# --- Docker-Log-Rotation (damit VPS nicht voll läuft) -----------------------
log "Konfiguriere Docker-Log-Rotation (max 100 MB pro Datei, 3 Rotationen)"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
JSON
systemctl restart docker

# --- Firewall ---------------------------------------------------------------
log "Konfiguriere UFW (SSH, HTTP, HTTPS)"
ufw --force reset >/dev/null 2>&1 || true
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# --- Nicht-Root-User kann Docker nutzen (falls via sudo aufgerufen) ---------
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  log "Füge $SUDO_USER zur docker-Gruppe hinzu (neues Login nötig)"
  usermod -aG docker "$SUDO_USER"
fi

# --- Swap sicherstellen (wichtig für kleinere VPS mit LLM-Inferenz) --------
if [ "$(swapon --show | wc -l)" -eq 0 ]; then
  log "Kein Swap gefunden — lege 4 GB Swapfile an"
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  log "Swap bereits aktiv — überspringe"
fi

# --- Fertig -----------------------------------------------------------------
log "Fertig."
echo
echo "Nächste Schritte:"
echo "  1. Logge Dich neu ein (damit die Docker-Gruppenmitgliedschaft aktiv wird)"
echo "  2. Code nach /opt/rag bringen (git clone ODER rsync)"
echo "  3. cd /opt/rag && cp .env.example .env && nano .env"
echo "  4. docker compose up -d --build"
echo "  5. make models    # pullt BGE-M3 + Qwen 2.5 7B"
echo "  6. make health"
