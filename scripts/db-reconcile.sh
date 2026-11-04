#!/usr/bin/env bash
# =============================================================
#  Parastoo DB credential reconcile — خودترمیمی رمزهای دیتابیس
#
#  مشکل واقعی (v0.2.14): volume پستگرس رمز «اولین نصب» را نگه می‌دارد؛
#  اسکریپت‌های init فقط بار اولِ volume خالی اجرا می‌شوند. اگر .env به هر
#  دلیل از volume جلو بزند (کرش نصب‌ر وسط نوشتن .env + بازتولید رمزها)،
#  گیتا/کر با «password authentication failed» بالا نمی‌آیند، گیتا
#  unhealthy می‌شود و کل استک هرگز استارت نمی‌خورد.
#
#  راه‌حل: .env منبع حقیقت است — پیش از `compose up`، رمز کاربران داخل
#  دیتابیس با .env همگام می‌شود. idempotent؛ همیشه exit 0 (بهترین تلاش،
#  هرگز نصب/آپدیت را متوقف نمی‌کند).
#
#  اجرا: خودکار از install.sh و scripts/auto-update.sh (پیش از compose up)
#        دستی:  sudo bash /opt/parastoo/scripts/db-reconcile.sh
# =============================================================
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 0

say()  { echo "[db-reconcile] $*"; }
warn() { echo "[db-reconcile] WARN: $*" >&2; }

# بدون docker-compose / docker هیچ کاری نمی‌توان کرد — بی‌صدا رد شو
# (مثلاً اجرای تستی خارج از سرور). exit 0 تا مسیر نصب نشکند.
[ -f docker-compose.yml ] || exit 0
command -v docker >/dev/null 2>&1 || exit 0
docker compose version >/dev/null 2>&1 || exit 0

# ---------- رمزها را بدون source کردن از .env بخوان ----------
# source کردن .env قدیمی با set -e کشنده است (خط unquoted با فاصله =
# «command not found»). grep/cut هیچ‌وقت اجرا نمی‌شود و فضای خالی/کوتیشن را می‌پزد.
env_val() {
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env 2>/dev/null | head -n1 | tr -d '\r' \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
          -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}
PG_PASS="$(env_val POSTGRES_PASSWORD)"
GT_PASS="$(env_val GITEA_DB_PASSWORD)"
if [ -z "$PG_PASS" ] || [ -z "$GT_PASS" ]; then
  warn ".env lacks POSTGRES_PASSWORD/GITEA_DB_PASSWORD; nothing to reconcile (fresh-install path initializes the volume from .env)."
  exit 0
fi

# ---------- پستگرس را تنها بالا بیاور و سالم شدنش را صبر کن ----------
say "starting postgres alone for the credential check..."
docker compose up -d postgres >/dev/null 2>&1 || { warn "postgres failed to start; skipping reconcile"; exit 0; }
HEALTHY=""
for _i in $(seq 1 45); do
  HEALTHY="$(docker inspect -f '{{.State.Health.Status}}' parastoo-postgres 2>/dev/null || echo unknown)"
  [ "$HEALTHY" = "healthy" ] && break
  sleep 2
done
if [ "$HEALTHY" != "healthy" ]; then
  warn "postgres not healthy within 90s; skipping reconcile (compose up will report the real error)."
  exit 0
fi

# psql با متغیر + :'pw' -> رمز هر کاراکتری که باشد امن کوتیشن می‌شود (SQL-injection-proof)
psql_exec() { docker compose exec -T postgres psql -U parastoo -d parastoo -v ON_ERROR_STOP=1 "$@"; }

# ---------- ۱) کاربر ابرکاربر parastoo (DATABASE_URL کر از آن استفاده می‌کند) ----------
if psql_exec -v pw="$PG_PASS" -c "ALTER USER parastoo WITH PASSWORD :'pw';" >/dev/null 2>&1; then
  say "user 'parastoo': password aligned with .env"
else
  warn "could not align password of user 'parastoo'"
fi

# ---------- ۲) کاربر gitea ----------
if [ "$(psql_exec -tAc "SELECT 1 FROM pg_roles WHERE rolname='gitea'" 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
  if psql_exec -v pw="$GT_PASS" -c "ALTER USER gitea WITH PASSWORD :'pw';" >/dev/null 2>&1; then
    say "user 'gitea': password aligned with .env"
  else
    warn "could not align password of user 'gitea'"
  fi
else
  # volume تازه init شده اما init script اجرا نشده (یا نیمه‌کاره مانده) — همان کاری
  # که postgres-init/01-gitea.sh می‌کند، اینجا با رمز .env انجام می‌دهیم
  say "user 'gitea' missing — creating database gitea + user (first-init equivalent)"
  psql_exec -v pw="$GT_PASS" -c "CREATE USER gitea WITH PASSWORD :'pw';" >/dev/null 2>&1 || warn "CREATE USER gitea failed"
  psql_exec -c "CREATE DATABASE gitea OWNER gitea;" >/dev/null 2>&1 || true   # ممکن است از قبل باشد
  psql_exec -c "GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea;" >/dev/null 2>&1 || true
  psql_exec -c "ALTER DATABASE gitea OWNER TO gitea;" >/dev/null 2>&1 || true
fi

say "db credential reconcile done"
exit 0
