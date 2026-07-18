#!/usr/bin/env bash
# =============================================================================
# Generiert kryptographisch sichere Secrets für die .env
# Auf VPS oder lokal ausführbar (braucht nur openssl).
# =============================================================================

set -euo pipefail

echo "# --- Secrets für .env — generiert am $(date -Iseconds) ---"
echo
echo "APP_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n' | tr '/+' '_-')"
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '\n' | tr '/+' '_-')"
echo "QDRANT_API_KEY=$(openssl rand -base64 32 | tr -d '\n' | tr '/+' '_-')"
echo "ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '\n' | tr '/+' '_-')"
echo
echo "# Kopiere diese Zeilen in Deine .env"
echo "# ADMIN_PASSWORD notieren! Es ist das initiale UI-Passwort."
