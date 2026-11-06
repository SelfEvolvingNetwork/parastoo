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
  # site containers live OUTSIDE the compose stack (created via Docker SDK,
  # restart=unless-stopped) — compose down never touches them, remove first
  echo "Removing deployed site containers..."
  docker ps -aq --filter name=parastoo-site- | xargs -r docker rm -f
  docker compose down -v --remove-orphans
  echo "Removing project images..."
  # -n1 + tolerate failure: layered images may already be gone as dependencies
  # of an earlier removal — a hard error here aborted nothing important but
  # scared the user with "No such image" (v0.3.24)
  docker images -q --filter label=parastoo.slug | xargs -r -n1 docker rmi -f 2>/dev/null || true
else
  docker compose down --remove-orphans
fi

rm -f /usr/local/bin/parastoo "$DIR_FILE"
# آپدیت خودکار (v0.2.12) — تایمر و واحد systemd هم پاک شود
if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now parastoo-autoupdate.timer 2>/dev/null || true
  rm -f /etc/systemd/system/parastoo-autoupdate.service /etc/systemd/system/parastoo-autoupdate.timer
  systemctl daemon-reload 2>/dev/null || true
fi
echo -e "${GREEN}Parastoo removed. The install folder remains at $APP_DIR (files and .env).${NC}"
