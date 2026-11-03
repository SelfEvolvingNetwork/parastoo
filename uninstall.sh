#!/usr/bin/env bash
# حذف کامل پرستو از سرور (با پرس‌وجو)
set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

[ "$(id -u)" -eq 0 ] || { echo "با sudo اجرا کنید."; exit 1; }
DIR_FILE="/etc/parastoo-install-path"
APP_DIR="$(cat "$DIR_FILE" 2>/dev/null || echo "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
cd "$APP_DIR"

echo -e "${RED}هشدار: این کار همه سرویس‌ها را متوقف می‌کند.${NC}"
read -r -p "پروژه‌ها و مخزن‌ها (volume ها) هم حذف شوند؟ [y/N]: " DEL_VOLS || true
read -r -p "مطمئن هستید؟ [y/N]: " CONFIRM || true
[ "${CONFIRM:-n}" = "y" ] || { echo "لغو شد."; exit 0; }

if [ "${DEL_VOLS:-n}" = "y" ]; then
  docker compose down -v --remove-orphans
else
  docker compose down --remove-orphans
fi

rm -f /usr/local/bin/parastoo "$DIR_FILE"
echo -e "${GREEN}پرستو حذف شد. پوشه نصب در $APP_DIR باقی ماند (فایل‌ها و .env).${NC}"
