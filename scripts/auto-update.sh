#!/usr/bin/env bash
# =============================================================
#  Parastoo auto-update — پل گیت‌هاب به سرور (موتور مشترک آپدیت)
#
#  اگر شاخهٔ کانال (پیش‌فرض main) در گیت‌هاب جلو رفته باشد، کد جدید
#  دانلود، به‌صورت امن جایگزین و استک داکر بازسازی می‌شود.
#
#  v0.5.12 — قرار مالک «یک روال شفاف»: استقرار فقط با فرمان صریح است و این
#  اسکریپت دیمونِ همیشگی نیست؛ سه فراخوانی مجاز دارد:
#    1) sudo parastoo update                       (همان --force)
#    2) lifecycle پل: POST /api/admin/parastoo/update
#    3) تایمر: sudo parastoo autoupdate on
#
#  v0.5.13 — فرمان تازهٔ مالک (2026-11-09): «بروزرسانی پرستو و پل باید اتوماتیک
#  باشد و نباید پرسیده شود» — تایمرِ مسیر ۳ از این تاریخ در نصبِ تازه پیش‌فرض
#  مسلح است (install.sh §6f، پس از سلامتِ استک)؛ نصب/حذف/نصب مجدد همچنان فقط
#  با فرمان صریح کاربر. خاموش‌کردن: sudo parastoo autoupdate off. تمام
#  محافظ‌ها سر جایشان‌اند: flock، بکاپ پیش از جایگزینی، مهر فقط پس از سلامتِ
#  کانتینر، backoff ده‌دقیقه‌ای پس از شکست، حداقل فاصلهٔ بررسی.
#
#  پیکربندی (در .env کنار همین اسکریپت):
#    PARASTOO_AUTO_UPDATE=true|false     کلید حالت خودکار (پیش‌فرض true؛ خاموش‌کردن: sudo parastoo autoupdate off)
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

# record_fail <detail> — ثبت شکست + backoff ده‌دقیقه‌ای؛ میدان 2026-11-08: وقتی
# مهرِ استقرار فقط پس از موفقیت زده می‌شود، شکستِ تکرارشدنی است و بیلدِ خراب نباید
# هر تیک از نو اجرا شود (تیک بعدیِ پس از شکست بلافاصله تلاش نکند؛ --force مستثناست)
record_fail() {
  date +%s > "$APP_DIR/.autoupdate-backoff" 2>/dev/null || true
  record "failed" "$1"
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

# پس از یک شکست، ده دقیقه دست‌نگه‌داشتن (--force همیشه مستثناست)
LASTFAIL=0
[ -f "$APP_DIR/.autoupdate-backoff" ] && LASTFAIL="$(head -n1 "$APP_DIR/.autoupdate-backoff" 2>/dev/null || echo 0)"
case "$LASTFAIL" in ''|*[!0-9]*) LASTFAIL=0 ;; esac
if [ "$FORCE" -ne 1 ] && [ $((NOW - LASTFAIL)) -lt 600 ]; then
  exit 0
fi

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
  record_fail "download from github failed"
  exit 1
fi

mkdir -p "$WORK/src"
tar -xzf "$WORK/pkg.tar.gz" -C "$WORK/src" 2>>"$LOG" || { record_fail "tar extract failed"; exit 1; }
SRC="$(find "$WORK/src" -maxdepth 2 -name docker-compose.yml -printf '%h\n' 2>/dev/null | head -n1)"
if [ -z "$SRC" ] || [ ! -f "$SRC/install.sh" ] || [ ! -f "$SRC/core/app/config.py" ]; then
  log "ERROR: invalid package (docker-compose.yml / install.sh / core missing)"
  record_fail "invalid package layout"
  exit 1
fi
NEW_VER="$(sed -n 's/^ *VERSION: *str *= *"\([^"]*\)".*/\1/p' "$SRC/core/app/config.py" | head -1)"

# ---------- ۳) بکاپ کد فعلی (بیمهٔ بازگشت) ----------
# میدان 2026-11-08 (نصب تازه): auto-update قبل از install.sh اجرا شد و backups/ هنوز
# وجود نداشت؛ tar شکست خورد ولی لاگ «موفق» می‌نوشت و فایل بکاپ اصلاً ساخته نشده بود.
# اکنون پوشه ساخته می‌شود، خروجی tar چک می‌شود و شکستِ بکاپ یعنی توقفِ امن پیش از
# جایگزینی کد. نگه‌داشت ۱۰ بکاپ آخر هم اضافه شد تا تلاش‌های مکرر پُرش نکند.
BK="backups/code-$(date +%Y%m%d-%H%M%S).tar.gz"
mkdir -p backups
if tar czf "$BK" --exclude='backups' --exclude='offline-images' --exclude='logs' \
      --exclude='config' --exclude='.env' --exclude='*.tar.gz' \
      core bin deploy scripts postgres-init docs *.sh docker-compose.yml README.md 2>>"$LOG"; then
  log "code backup: $BK"
  ls -1t backups/code-*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
else
  log "ERROR: code backup failed — update aborted, current code untouched"
  record_fail "code backup failed"
  exit 1
fi

# ---------- ۴) جایگزینی امن کد (مثل setup.sh: فقط کد، نه داده) ----------
rm -rf core bin deploy scripts postgres-init docs
cp -a "$SRC/core" "$SRC/bin" "$SRC/deploy" "$SRC/scripts" "$SRC/postgres-init" "$SRC/docs" ./ || { record_fail "copy code failed"; exit 1; }
for f in docker-compose.yml install.sh setup.sh bootstrap.sh uninstall.sh README.md LICENSE .env.example; do
  [ -f "$SRC/$f" ] && cp -f "$SRC/$f" ./
done
chmod +x scripts/*.sh bin/parastoo install.sh setup.sh uninstall.sh bootstrap.sh 2>/dev/null || true
rm -rf core/app/__pycache__ core/app/routes/__pycache__ scripts/__pycache__ 2>/dev/null || true

# ---------- ۵) همگامی متادیتای استقرار (بدون مهر) ----------
# میدان 2026-11-08: مهر .parastoo-deploy قبلاً همین‌جا (قبل از compose) نوشته می‌شد؛
# اگر بیلد/up شکست می‌خورد، مهرِ کامیت جدید باقی می‌ماند، چرخهٔ بعد «هیچ‌کار نکن»
# می‌رفت و سرور با کدِ قدیمی و وضعیت کاذب رها می‌شد. اکنون مهر فقط در گام ۶ پس از
# سلامتِ کانتینر تازه زده می‌شود تا شکست یعنی تلاش مجدد. مقدارهای env همچنان همین‌جا
# ست می‌شوند چون compose موقع up متادیتا را از محیط/فایل می‌خواند (اولویت محیط >
# فایل) و کانتینر تازه باید کامیت جدید را از همان ابتدا ببیند (درس v0.3.21).
NOW_H="$(date '+%F %T')"
set_env PARASTOO_DEPLOYED_COMMIT "$REMOTE"
set_env PARASTOO_DEPLOYED_AT "$NOW_H"
# v0.3.21 (درس بازبینی زنده): اسکریپت در ابتدا (.env) را با set -a صادر کرده؛
# مقدارهای کهنه در محیط شل می‌مانند و compose موقع up به‌جای فایل از محیط شل
# می‌خواند (اولویت محیط > فایل) — نتیجه: healthz یک چرخه عقب می‌افتاد.
# صریحاً مقدار تازه را صادر می‌کنیم تا کانتینر همان‌جا متادیتای درست بگیرد.
export PARASTOO_DEPLOYED_COMMIT="$REMOTE"
export PARASTOO_DEPLOYED_AT="$NOW_H"
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
    # v0.3.21+2 — خودآزمایی: کانتینرِ تازه باید متادیتای همین کامیت را ببیند.
    # (درس همان باگ export: اگر روزی مسیر env عوض شود، این هشدار در log/update.log
    # فوراً پیدایش می‌کند، نه چند چرخه بعد)
    _hz="$(docker compose exec -T core curl -fsS http://localhost:8000/healthz 2>/dev/null || true)"
    case "$_hz" in
      *"\"commit\":\"$REMOTE\""*) ;;
      *) log "WARN: healthz metadata mismatch — container may carry stale env: $(printf '%s' "$_hz" | head -c 200)" ;;
    esac
    # مهر استقرار — فقط پس از تأیید سلامتِ کانتینر تازه (میدان 2026-11-08)؛
    # زمانِ مهر همان NOW_H گام ۵ است تا با PARASTOO_DEPLOYED_AT در .env یکسان بماند
    {
      echo "version=${NEW_VER:-unknown}"
      echo "commit=${REMOTE}"
      echo "time=${NOW_H}"
    } > "$DEPLOY"
    # v0.5.27 — آمادگی ری‌استارت (فرمان مالک، پس از اولین ری‌استارت واقعی سرور):
    # تازه‌سازی یونیت همگرایی بوت (parastoo-stack.service) با هر آپدیت موفق —
    # ایدمپوتنت و best-effort؛ خودآپدیت از مخزن می‌آید و install.sh را اجرا
    # نمی‌کند، پس بدون این گام یونیت هرگز روی سرورهای موجود نصب نمی‌شد.
    if command -v systemctl >/dev/null 2>&1 \
       && [ -f "deploy/systemd/parastoo-stack.service" ]; then
      sed "s|__INSTALL_DIR__|$PWD|g" "deploy/systemd/parastoo-stack.service" \
        > /etc/systemd/system/parastoo-stack.service 2>/dev/null \
        && systemctl daemon-reload >/dev/null 2>&1 \
        && systemctl enable parastoo-stack.service >/dev/null 2>&1 \
        && log "boot convergence unit refreshed+enabled (parastoo-stack.service)" \
        || log "WARN: could not refresh parastoo-stack.service — after a reboot run: sudo systemctl enable parastoo-stack.service"
    fi
    record "ok" "deployed ${REMOTE:0:12} (v${NEW_VER:-?})"
    log "update complete: v${NEW_VER:-?} (${REMOTE:0:12})"
    exit 0
  fi
  record_fail "core not healthy after deploy — see logs/update.log"
  log "ERROR: core did not become healthy; run: docker compose logs --tail 50 core"
  exit 1
fi
record_fail "docker compose build/up failed — see logs/update.log"
log "ERROR: docker compose failed; new code is on disk (rollback backup: $BK)"
exit 1
