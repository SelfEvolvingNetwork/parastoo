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
#  دیتابیس با .env همگام می‌شود. idempotent؛ best-effort (همیشه exit 0،
#  هرگز نصب/آپدیت را متوقف نمی‌کند) و هر شکست، «علت واقعی psql» را چاپ
#  می‌کند تا لاگ bootstrap خودتشخیص باشد (درس v0.2.15: خطا را بی‌صدا نکن؛
#  روش -v pw=... + :'pw' روی سرور بی‌علت شکست خورد — این نسخه SQL را با
#  dollar-quoting از stdin می‌فرستد و در پایان ورود TCP را واقعاً آزمون می‌کند).
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
[ -f docker-compose.yml ] || exit 0
command -v docker >/dev/null 2>&1 || exit 0
docker compose version >/dev/null 2>&1 || exit 0

# ---------- رمزها را بدون source کردن از .env بخوان ----------
# source کردن .env قدیمی با set -e کشنده است (خط unquoted با فاصله =
# «command not found»). grep/sed هیچ‌وقت اجرا نمی‌شوند و کوتیشن/فاصله را می‌پزد.
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

# psql_stdin — SQL از stdin با dollar-quoting: هیچ کوتیشن/کاراکتر خاصی در
# رمز نمی‌شکند و به interpolation داخلی psql هم وابسته نیست. خروجی psql
# (stdout+stderr) برگردانده می‌شود؛ rc معیار موفقیت است.
psql_stdin() { docker compose exec -T postgres psql -U parastoo -d parastoo -v ON_ERROR_STOP=1 2>&1; }

alter_pw() {  # alter_pw <role> <newpass> — خروجی فقط در شکست چاپ می‌شود
  local _out
  _out="$(psql_stdin <<PSQL
ALTER ROLE $1 WITH PASSWORD \$prstoo\$$2\$prstoo\$;
PSQL
)" && return 0
  printf '%s' "$_out"
  return 1
}

# auth_ok <user> <pass> — آزمون واقعی ورود از راه TCP (scram)، دقیقاً همان
# مسیری که گیتا و کر طی می‌کنند؛ فقط رمز درست قبول می‌شود
auth_ok() {
  docker compose exec -T postgres env PGPASSWORD="$2" \
    psql -h 127.0.0.1 -U "$1" -d postgres -tAc 'SELECT 1' >/dev/null 2>&1
}

# ---------- ۱) کاربر ابرکاربر parastoo (DATABASE_URL کر از آن استفاده می‌کند) ----------
if _err="$(alter_pw parastoo "$PG_PASS")"; then
  say "user 'parastoo': password aligned with .env"
else
  warn "could not align password of user 'parastoo': ${_err:-psql failed (no output)}"
fi

# ---------- ۲) کاربر gitea ----------
if [ "$(docker compose exec -T postgres psql -U parastoo -d parastoo -tAc "SELECT 1 FROM pg_roles WHERE rolname='gitea'" 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
  if _err="$(alter_pw gitea "$GT_PASS")"; then
    say "user 'gitea': password aligned with .env"
  else
    warn "could not align password of user 'gitea': ${_err:-psql failed (no output)}"
  fi
else
  # volume تازه init شده اما init script اجرا نشده (یا نیمه‌کاره مانده) — همان کاری
  # که postgres-init/01-gitea.sh می‌کند، اینجا با رمز .env انجام می‌دهیم
  say "user 'gitea' missing — creating database gitea + user (first-init equivalent)"
  _err="$(psql_stdin <<PSQL
CREATE USER gitea WITH PASSWORD \$prstoo\$$GT_PASS\$prstoo\$;
PSQL
)" || warn "CREATE USER gitea failed: ${_err:-no output}"
  _err="$(psql_stdin <<'PSQL'
CREATE DATABASE gitea OWNER gitea;
PSQL
)" || { case "$_err" in *"already exists"*) : ;; *) warn "CREATE DATABASE gitea failed: ${_err:-no output}" ;; esac; }
  _err="$(psql_stdin <<'PSQL'
GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea;
ALTER DATABASE gitea OWNER TO gitea;
PSQL
)" || warn "GRANT/OWNER gitea failed: ${_err:-no output}"
fi

# ---------- ۳) تأیید نهایی: هر دو کاربر باید از راه TCP وارد شوند ----------
if auth_ok parastoo "$PG_PASS" && auth_ok gitea "$GT_PASS"; then
  say "verified: parastoo + gitea authenticate over TCP with the .env passwords"
else
  warn "post-reconcile TCP auth check FAILED for at least one user; gitea/core may still report 'password authentication failed'."
fi

say "db credential reconcile done"
exit 0
