#!/usr/bin/env bash
# Uninstall Parastoo completely (with confirmation)
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
DIR_FILE="/etc/parastoo-install-path"
APP_DIR="$(cat "$DIR_FILE" 2>/dev/null || echo "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
cd "$APP_DIR"

echo -e "${RED}WARNING: this stops all Parastoo services.${NC}"
read -r -p "Also delete projects and repositories (Docker volumes)? [y/N]: " DEL_VOLS || true
read -r -p "Are you sure? [y/N]: " CONFIRM || true
[ "${CONFIRM:-n}" = "y" ] || { echo "Cancelled."; exit 0; }

if [ "${DEL_VOLS:-n}" = "y" ]; then
  docker compose down -v --remove-orphans
else
  docker compose down --remove-orphans
fi

rm -f /usr/local/bin/parastoo "$DIR_FILE"
echo -e "${GREEN}Parastoo removed. The install folder remains at $APP_DIR (files and .env).${NC}"
