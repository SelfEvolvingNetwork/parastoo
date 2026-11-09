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
  # project containers live OUTSIDE the compose stack (created via Docker SDK,
  # restart=unless-stopped) — compose down never touches them, remove first.
  # v0.4.0 naming: parastoo-p<id>-<svc> with label parastoo.project=<id>;
  # legacy pre-0.4 sites are parastoo-site-<slug> — both are swept here.
  echo "Removing deployed project containers..."
  docker ps -aq --filter name=parastoo-site- | xargs -r docker rm -f
  docker ps -aq --filter label=parastoo.project | xargs -r docker rm -f
  docker network ls -q --filter name=parastoo-proj- | xargs -r docker network rm
  docker compose down -v --remove-orphans
  echo "Removing project images..."
  # -n1 + tolerate failure: layered images may already be gone as dependencies
  # of an earlier removal — a hard error here aborted nothing important but
  # scared the user with "No such image" (v0.3.24)
  docker images -q --filter label=parastoo.slug | xargs -r -n1 docker rmi -f 2>/dev/null || true
  # user volumes of project services (parastoo-p<id>-<name>) — compose prefix
  # is "parastoo_" (underscore) so platform volumes never match this filter.
  # v0.5.8 field lesson (2026-11-08, 5th fresh install): when the user has no
  # project volumes, `grep -v` selects nothing and exits 1 — under
  # `set -euo pipefail` the whole uninstall died right HERE, before removing
  # the CLI and before disabling the auto-update/tls-store timers. The still-
  # armed timer then raced the next fresh bootstrap mid-install (auto-update
  # ran `docker compose up` with a half-copied tree and no .env) — the exact
  # root cause of the broken 06:36 install of the same day. The sweep must be
  # failure-tolerant: an empty sweep is SUCCESS, not an error.
  USER_VOLS="$(docker volume ls -q --filter name=parastoo-p | grep -v '^parastoo_' || true)"
  # `|| true` on the rm too: a volume that refuses to die (in use) must still
  # not abort the uninstall before the CLI/timer cleanup below runs.
  echo "$USER_VOLS" | xargs -r docker volume rm || true
else
  docker compose down --remove-orphans
fi

rm -f /usr/local/bin/parastoo "$DIR_FILE"
# آپدیت خودکار (v0.2.12) — تایمر و واحد systemd هم پاک شود
if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now parastoo-autoupdate.timer 2>/dev/null || true
  systemctl disable --now parastoo-tls-store.timer 2>/dev/null || true
  rm -f /etc/systemd/system/parastoo-autoupdate.service /etc/systemd/system/parastoo-autoupdate.timer \
        /etc/systemd/system/parastoo-tls-store.service /etc/systemd/system/parastoo-tls-store.timer
  systemctl daemon-reload 2>/dev/null || true
fi
echo -e "${GREEN}Parastoo removed. The install folder remains at $APP_DIR (files and .env).${NC}"
# قانون گواهی ماندگار (v0.3.32): فروشگاه گواهی عمداً حفظ می‌شود تا نصب بعدی
# چیزی از CA دوباره نخواهد؛ حذفش فقط دستی و با آگاهی کامل.
echo -e "${GREEN}TLS certificate store kept at /var/lib/parastoo/tls (the certificate law — delete manually only if you truly mean it).${NC}"
# v0.5.14 — هویت دامنهٔ واقعی عمداً در /etc می‌ماند (بیرون از درخت نصب):
# دیالگیشن رجیستری همچنان به این سرور اشاره می‌کند و نصب بعدی خودش همان
# دامنه را با زون و HTTPS زنده می‌کند (فرمان مالک 2026-11-09: «با نصب، دامنه
# هم اتوماتیک فعال شود»). شروعِ کاملاً سفید = حذف دستی همین یک فایل.
echo -e "${GREEN}Domain identity kept at /etc/parastoo-domain (the next install re-activates the same domain automatically — delete it only for a truly blank slate).${NC}"
