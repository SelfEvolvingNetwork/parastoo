#!/usr/bin/env bash
# =============================================================
#  Parastoo auto-update — پل خودکار گیت‌هاب به سرور
#
#  هر بار که شاخهٔ کانال (پیش‌فرض main) در گیت‌هاب جلو برود، کد جدید
#  دانلود، به‌صورت امن جایگزین و استک داکر بازسازی می‌شود. نصبِ اولیه
#  دستی می‌ماند؛ آپدیت‌های بعدی خودکار است.
#
#  اجرا: توسط تایمر systemd (deploy/systemd/parastoo-autoupdate.timer)
#        یا دستی:  sudo bash /opt/parastoo/scripts/auto-update.sh --force
#
#  پیکربندی (در .env کنار همین اسکریپت):
#    PARASTOO_AUTO_UPDATE=true|false     کلید اصلی (پیش‌فرض true)
#    PARASTOO_AUTO_UPDATE_INTERVAL=120   حداقل فاصلهٔ بررسی (ثانیه، ≥60)
#    PARASTOO_UPDATE_CHANNEL=main        شاخه یا تگ هدف
#    PARASTOO_GH_PROXY=                  پیشوند پراکسی گیت‌هاب (تحریم‌ها)
#
#  حفاظت‌ها:
#    - flock: هرگز دو نسخه هم‌زمان اجرا نمی‌شود
#    - .env / config/ / backups/ / offline-images/ / logs/ هرگز دست نمی‌خورند
#    - پیش از جایگزینی کد، بکاپ در backups/code-<زمان>.tar.gz
#    - تاریخچهٔ هر استقرار در logs/updates.jsonl (خوانده می‌شود توسط /api/dev/updates)
#
#  نکتهٔ فنی: جایگزینی scripts/ با rm+cp انجام می‌شود (inode جدید)؛
#  bash اسکریپتِ در حال اجرا را از fd قدیمی می‌خواند و نصفه قطع نمی‌شود.
# =============================================================
set -uo pipefail

ORG="${PARASTOO_GH_ORG:-SelfEvolvingNetwork}"
REPO="${PARASTOO_GH_REPO:-parastoo}"

APP_DIR="/opt/parastoo"
[ -f /etc/parastoo-install-path ] && APP_DIR="$(cat /etc/parastoo-install-path)"
if [ ! -f "$APP_DIR/docker-compose.yml" ]; then
  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
cd "$APP_DIR" || exit 1

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

mkdir -p logs
LOG=logs/update.log
HIST=logs/updates.jsonl
STATE="$APP_DIR/.autoupdate-state"     # آخرین زمان بررسی
DEPLOY="$APP_DIR/.parastoo-deploy"     # نسخه/کامیت مستقر

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# record <result> <detail> — یک خط JSON به تاریخچه (مصرف /api/dev/updates)
record() {
  local detail
  detail="$(printf '%s' "${2:-}" | tr '\t\n' '  ' | sed 's/"/\\"/g' | cut -c1-300)"
  printf '{"time":"%s","from":"%s","to":"%s","version":"%s","result":"%s","detail":"%s"}\n' \
    "$(date '+%F %T')" "${CURRENT:-}" "${REMOTE:-}" "${NEW_VER:-}" "$1" "$detail" >> "$HIST" 2>/dev/null || true
}

# set_env KEY VALUE — نوشتن idempotent در .env (بدون خرابکردن بقیه خط‌ها)
# v0.2.15: مقدار همیشه کوتیشن می‌شود (DEPLOYED_AT فاصله دارد؛ خط unquoted در
# اجرای بعدی، source کردن .env را می‌کشد) و کاراکترهای خاص sed هم escape می‌شوند
set_env() {
  [ -f .env ] || return 0
  local esc
  esc="$(printf '%s' "$2" | sed -e 's/[&|\\]/\\&/g')"
  if grep -qE "^$1=" .env; then
    sed -i "s|^$1=.*|$1=\"$esc\"|" .env
  else
    printf '\n# --- written by auto-update ---\n%s="%s"\n' "$1" "$2" >> .env
  fi
}

# ---------- قفل: جلوگیری از اجرای موازی ----------
if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/parastoo-autoupdate.lock
  flock -n 9 || { log "another auto-update is running; skipping"; exit 0; }
fi

# ---------- پیکربندی از .env ----------
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ "${PARASTOO_AUTO_UPDATE:-true}" != "true" ] && [ "$FORCE" -ne 1 ]; then
  exit 0   # آپدیت خودکار خاموش است (parastoo update همچنان کار می‌کند)
fi

INTERVAL="${PARASTOO_AUTO_UPDATE_INTERVAL:-120}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=120 ;; esac
[ "$INTERVAL" -lt 60 ] && INTERVAL=60

# کنترل فاصله (تایمر هر ۶۰ ثانیه صدا می‌زند؛ اینجا خودمان را کم‌کار می‌کنیم)
NOW="$(date +%s)"
LAST=0
[ -f "$STATE" ] && LAST="$(head -n1 "$STATE" 2>/dev/null || echo 0)"
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
if [ "$FORCE" -ne 1 ] && [ $((NOW - LAST)) -lt "$INTERVAL" ]; then
  exit 0
fi
echo "$NOW" > "$STATE"

# ---------- ۱) بررسی کامیت جدید در گیت‌هاب ----------
REF="${PARASTOO_UPDATE_CHANNEL:-main}"
PROXY="${PARASTOO_GH_PROXY:-}"
REMOTE=""
for spec in "refs/heads/$REF" "refs/tags/$REF"; do
  REMOTE="$(timeout 40 git ls-remote "${PROXY}https://github.com/${ORG}/${REPO}.git" "$spec" 2>/dev/null | awk 'NR==1{print $1}')"
  [ -n "$REMOTE" ] && break
done
if [ -z "$REMOTE" ]; then
  log "cannot reach github (${ORG}/${REPO} ref ${REF}); will retry next tick"
  exit 0   # شبکه موقتاً قطع است — خطا نیست، تایمر دوباره امتحان می‌کند
fi

CURRENT=""
[ -f "$DEPLOY" ] && CURRENT="$(sed -n 's/^commit=//p' "$DEPLOY" | head -n1)"
if [ "$FORCE" -ne 1 ] && [ "$REMOTE" = "$CURRENT" ]; then
  exit 0   # هیچ چیز عوض نشده
fi

log "new ${REF} commit: ${REMOTE:0:12} (deployed: ${CURRENT:-none}) — updating"

# ---------- ۲) دانلود بستهٔ جدید ----------
WORK="$(mktemp -d /tmp/parastoo-autoupd.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
DL_OK=0
# مسیر ۱: کدلود با کامیت دقیق
timeout 300 curl -fsSL --retry 2 --max-time 280 -o "$WORK/pkg.tar.gz" \
  "${PROXY}https://codeload.github.com/${ORG}/${REPO}/tar.gz/${REMOTE}" 2>>"$LOG" && DL_OK=1
# مسیر ۲ (فالبک تحریم‌ها): آرشیو با نام رفرنس
if [ "$DL_OK" -ne 1 ]; then
  log "codeload failed; trying archive/${REF}.tar.gz"
  timeout 300 curl -fsSL --retry 2 --max-time 280 -o "$WORK/pkg.tar.gz" \
    "${PROXY}https://github.com/${ORG}/${REPO}/archive/${REF}.tar.gz" 2>>"$LOG" && DL_OK=1
fi
if [ "$DL_OK" -ne 1 ]; then
  log "ERROR: download from github failed"
  record "failed" "download from github failed"
  exit 1
fi

mkdir -p "$WORK/src"
tar -xzf "$WORK/pkg.tar.gz" -C "$WORK/src" 2>>"$LOG" || { record "failed" "tar extract failed"; exit 1; }
SRC="$(find "$WORK/src" -maxdepth 2 -name docker-compose.yml -printf '%h\n' 2>/dev/null | head -n1)"
if [ -z "$SRC" ] || [ ! -f "$SRC/install.sh" ] || [ ! -f "$SRC/core/app/config.py" ]; then
  log "ERROR: invalid package (docker-compose.yml / install.sh / core missing)"
  record "failed" "invalid package layout"
  exit 1
fi
NEW_VER="$(sed -n 's/^ *VERSION: *str *= *"\([^"]*\)".*/\1/p' "$SRC/core/app/config.py" | head -1)"

# ---------- ۳) بکاپ کد فعلی (بیمهٔ بازگشت) ----------
BK="backups/code-$(date +%Y%m%d-%H%M%S).tar.gz"
tar czf "$BK" --exclude='backups' --exclude='offline-images' --exclude='logs' \
      --exclude='config' --exclude='.env' --exclude='*.tar.gz' \
      core bin deploy scripts postgres-init docs *.sh docker-compose.yml README.md 2>>"$LOG" || true
log "code backup: $BK"

# ---------- ۴) جایگزینی امن کد (مثل setup.sh: فقط کد، نه داده) ----------
rm -rf core bin deploy scripts postgres-init docs
cp -a "$SRC/core" "$SRC/bin" "$SRC/deploy" "$SRC/scripts" "$SRC/postgres-init" "$SRC/docs" ./ || { record "failed" "copy code failed"; exit 1; }
for f in docker-compose.yml install.sh setup.sh bootstrap.sh uninstall.sh README.md LICENSE .env.example; do
  [ -f "$SRC/$f" ] && cp -f "$SRC/$f" ./
done
chmod +x scripts/*.sh bin/parastoo install.sh setup.sh uninstall.sh bootstrap.sh 2>/dev/null || true
rm -rf core/app/__pycache__ core/app/routes/__pycache__ scripts/__pycache__ 2>/dev/null || true

# ---------- ۵) ثبت وضعیت استقرار ----------
NOW_H="$(date '+%F %T')"
{
  echo "version=${NEW_VER:-unknown}"
  echo "commit=${REMOTE}"
  echo "time=${NOW_H}"
} > "$DEPLOY"
set_env PARASTOO_DEPLOYED_COMMIT "$REMOTE"
set_env PARASTOO_DEPLOYED_AT "$NOW_H"
cp -f bin/parastoo /usr/local/bin/parastoo 2>/dev/null || true
echo "$APP_DIR" 2>/dev/null > /etc/parastoo-install-path || true

# ---------- ۶) همگامی رمز دیتابیس + بازسازی و بالا آوردن استک ----------
# v0.2.15: اگر .env و volume پستگرس از هم جدا افتاده باشند (رمز بازتولید شده)،
# گیتا با "password authentication failed" unhealthy می‌شود و استک بالا نمی‌آید؛
# reconcile رمز داخل دیتابیس را با .env همگام می‌کند (best-effort، هرگز fatal نیست)
log "reconciling database credentials with .env..."
bash scripts/db-reconcile.sh >>"$LOG" 2>&1 || true
# کانتینرهای یتیمِ recreate نیمه‌کاره (ری‌استارت دیمون/شکستِ up قبلی) با نام موقت
# "<12hex>_parastoo-<svc>"، up بعدی را با «name Conflict» می‌بندند — اول پاکشان کن
STALE="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^[0-9a-f]{12}_parastoo-' || true)"
if [ -n "$STALE" ]; then
  log "removing stale containers from an interrupted recreate: $(echo "$STALE" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  docker rm -f $STALE >/dev/null 2>&1 || true
fi
log "rebuilding the stack (docker compose up -d --build)..."
if docker compose up -d --build >>"$LOG" 2>&1; then
  sleep 5   # recreate کامل شود؛ پاسخ کانتینرِ قدیمی را «سالم» نگیریم
  OK=0
  for i in $(seq 1 40); do
    if docker compose exec -T core curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then OK=1; break; fi
    sleep 3
  done
  if [ "$OK" = "1" ]; then
    record "ok" "deployed ${REMOTE:0:12} (v${NEW_VER:-?})"
    log "update complete: v${NEW_VER:-?} (${REMOTE:0:12})"
    exit 0
  fi
  record "failed" "core not healthy after deploy — see logs/update.log"
  log "ERROR: core did not become healthy; run: docker compose logs --tail 50 core"
  exit 1
fi
record "failed" "docker compose build/up failed — see logs/update.log"
log "ERROR: docker compose failed; new code is on disk (rollback backup: $BK)"
exit 1
