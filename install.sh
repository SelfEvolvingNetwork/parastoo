#!/usr/bin/env bash
# =============================================================
#  پرستو — نصب‌کنندهٔ تک‌فرمانی، سازگار با تحریم‌های ایران
#  استفاده:  sudo bash install.sh
#  نتیجه:    یک سرور کامل PaaS (پنل + گیت + رجیستری + میزبانی سایت)
#
#  لایه‌های ضدتحریم (خودکار، بدون هیچ سوالی):
#    ۱) نصب داکر: get.docker.com ← در صورت تحریم ← مخازن اوبونتو
#    ۲) ایمیج‌ها: Docker Hub ← در صورت تحریم ← آینه‌های ایرانی در daemon.json
#    ۳) آفلاین کامل: پوشهٔ offline-images/ کنار پکیج → docker load (بدون هیچ pull)
#  جزئیات: docs/SANCTIONS.md
# =============================================================
set -euo pipefail

# ---------- رنگ‌ها ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()   { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN} ✔${NC} $*"; }
warn()  { echo -e "${YELLOW} !${NC} $*"; }
die()   { echo -e "${RED} خطا:${NC} $*" >&2; exit 1; }

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$INSTALL_DIR"

[ "$(id -u)" -eq 0 ] || die "این اسکریپت باید با sudo اجرا شود."
[ -f docker-compose.yml ] || die "install.sh باید از ریشه پوشه پرستو اجرا شود."

# ---------- ۱) پیش‌نیازها ----------
say "بررسی پیش‌نیازها..."
if ! command -v curl >/dev/null 2>&1; then
  say "نصب curl..."
  apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
fi

# ---------- ۱الف) نصب داکر — با مسیر جایگزین تحریم ----------
install_docker_from_ubuntu() {
  # مخازن خود اوبونتو (ir.archive.ubuntu.com و آینه‌های داخلی) تحریم نیستند
  say "نصب داکر از مخازن اوبونتو..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose >/dev/null 2>&1 || return 1
  systemctl enable --now docker >/dev/null 2>&1 || true
  return 0
}

if ! command -v docker >/dev/null 2>&1; then
  say "داکر نصب نیست؛ نصب خودکار داکر..."
  # مسیر ۱: اسکریپت رسمی (روی سرور غیرتحریمی بهترین گزینه است — پلاگین compose را هم می‌دهد)
  if timeout 90 curl -fsSL --max-time 60 https://get.docker.com -o /tmp/get-docker.sh 2>/dev/null \
     && sh /tmp/get-docker.sh >/dev/null 2>&1; then
    ok "داکر از اسکریپت رسمی نصب شد."
  else
    # مسیر ۲: مخازن اوبونتو — از داخل ایران همیشه در دسترس است
    warn "get.docker.com در دسترس نیست (تحریم)؛ مسیر جایگزین: مخازن اوبونتو"
    install_docker_from_ubuntu \
      || die "نصب داکر ناموفق بود. راه‌حل دستی: docs/SANCTIONS.md بخش «نصب داکر»"
    ok "داکر از مخازن اوبونتو نصب شد."
  fi
else
  ok "داکر موجود است: $(docker --version)"
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if ! docker compose version >/dev/null 2>&1; then
  die "افزونه docker compose پیدا نشد. docker-compose-v2 را نصب کنید (docs/SANCTIONS.md)."
fi

# ---------- ۱ب) بستهٔ آفلاین ایمیج‌ها (اختیاری — مطمئن‌ترین مسیر) ----------
shopt -s nullglob
OFFLINE_TARS=(offline-images/*.tar offline-images/*.tar.gz parastoo-images*.tar.gz)
shopt -u nullglob
OFFLINE_LOADED=0
if [ ${#OFFLINE_TARS[@]} -gt 0 ]; then
  say "بستهٔ آفلاین ایمیج‌ها پیدا شد؛ بارگذاری مستقیم (بدون هیچ اتصال به Docker Hub)..."
  for tar in "${OFFLINE_TARS[@]}"; do
    if docker load -i "$tar"; then ok "بارگذاری شد: $tar"; OFFLINE_LOADED=1; else warn "بارگذاری ناموفق: $tar"; fi
  done
fi

# ---------- ۱ج) آینه‌های رجیستری — عبور از تحریم Docker Hub ----------
# سرویس‌های ایرانی (docker.ir، آروان‌کلاد، نوین‌کلاد و ...) فقط پیکربندی
# daemon.json می‌خواهند؛ لیست کاندیدها: deploy/mirrors.txt
# این بخش خودش: تشخیص تحریم ← تست زنده‌بودن آینه‌ها از داخل همین سرور
# ← نوشتن سالم‌ها به ترتیب سرعت ← تأیید با یک pull واقعی.

hub_reachable() {
  # endpoint استاندارد رجیستری: 401 یعنی در دسترس است (سریع؛ بدون pull)
  [ "$(timeout 12 curl -fsS -o /dev/null -w '%{http_code}' --max-time 8 https://registry-1.docker.io/v2/ 2>/dev/null)" = "401" ]
}

apply_mirrors() {
  local mirrors="$1"
  if command -v python3 >/dev/null 2>&1 && [ -f scripts/merge-docker-mirrors.py ]; then
    # shellcheck disable=SC2086
    python3 scripts/merge-docker-mirrors.py /etc/docker/daemon.json $mirrors || return 1
    systemctl restart docker >/dev/null 2>&1 || service docker restart >/dev/null 2>&1 || true
    sleep 2
    return 0
  fi
  return 1
}

if [ -n "${PARASTOO_REGISTRY_MIRRORS:-}" ]; then
  say "پیکربندی آینه‌ها از env (PARASTOO_REGISTRY_MIRRORS)..."
  apply_mirrors "$PARASTOO_REGISTRY_MIRRORS" \
    && ok "آینه‌ها تنظیم شد." \
    || die "تنظیم آینه‌ها ناموفق بود؛ /etc/docker/daemon.json را دستی ویرایش کنید (docs/SANCTIONS.md)."
elif hub_reachable; then
  ok "دسترسی به Docker Hub برقرار است (بدون آینه)."
else
  say "Docker Hub پاسخ نمی‌دهد (تحریم)؛ تست خودکار آینه‌های ایرانی از داخل سرور..."
  if [ "$OFFLINE_LOADED" = "1" ]; then
    # ایمیج‌های لازم از بستهٔ آفلاین آمده‌اند؛ آینه فقط برای آیندهٔ کاربران مفید است
    ALIVE="$(bash scripts/check-mirrors.sh 2>/dev/null || true)" || true
    if [ -n "$ALIVE" ]; then
      apply_mirrors "$ALIVE" && ok "آینه‌ها هم برای بیلدهای بعدی تنظیم شد." || true
    else
      warn "آینهٔ زنده‌ای پیدا نشد — نصب با بستهٔ آفلاین ادامه می‌یابد؛ برای بیلدهای بعدی docs/SANCTIONS.md را ببینید."
    fi
  else
    ALIVE="$(bash scripts/check-mirrors.sh || true)"
    if [ -n "$ALIVE" ]; then
      apply_mirrors "$ALIVE" \
        && ok "$(echo "$ALIVE" | wc -l) آینهٔ زنده در /etc/docker/daemon.json ثبت شد (سریع‌ترین اول)." \
        || warn "نوشتن آینه‌ها ناموفق بود — docs/SANCTIONS.md"
    else
      warn "هیچ آینه‌ای زنده نبود؛ لیست را به‌روز کنید: deploy/mirrors.txt یا PARASTOO_REGISTRY_MIRRORS"
      warn "بدون آینه، pull ایمیج‌ها شکست می‌خورد — مسیر مطمئن: بستهٔ آفلاین (docs/SANCTIONS.md لایهٔ ۳)"
    fi
    # تأیید نهایی: یک pull واقعی کوچک (همان ایمیجی که backup هم لازم دارد)
    if timeout 90 docker pull -q alpine:3.20 >/dev/null 2>&1; then
      ok "تأیید نهایی: pull از طریق آینه کار می‌کند."
    else
      warn "pull آزمایشی هم ناموفق بود. مسیر مطمئن: bash scripts/fetch-images.sh روی سرور خارج → پوشهٔ offline-images/ → دوباره install.sh"
    fi
  fi
fi

# ---------- ۲) دامنه پایه — بدون هیچ سوالی ----------
# نصب همیشه با دامنه موقت (nip.io) شروع می‌شود تا صفر سوال بپرسد؛
# دامنه واقعی و HTTPS بعداً از پنل وب (ویزارد دامنه) با توکن نصب تنظیم می‌شود.
BASE_DOMAIN="${PARASTOO_BASE_DOMAIN:-}"
say "تشخیص IP عمومی سرور..."
PUBLIC_IP="$(curl -fs4 --max-time 10 https://api.ipify.org 2>/dev/null \
          || curl -fs4 --max-time 10 https://ifconfig.me 2>/dev/null \
          || curl -fs4 --max-time 10 https://checkip.amazonaws.com 2>/dev/null \
          || curl -fs4 --max-time 10 https://icanhazip.com 2>/dev/null \
          || true)"
[ -n "$PUBLIC_IP" ] || die "IP عمومی شناسایی نشد. دستی بدهید: PARASTOO_PUBLIC_IP=1.2.3.4 PARASTOO_BASE_DOMAIN=example.com bash install.sh"
if [ -z "$BASE_DOMAIN" ]; then
  BASE_DOMAIN="${PUBLIC_IP}.nip.io"
  ok "دامنه موقت: ${BASE_DOMAIN} (دامنه واقعی بعداً از پنل وب تنظیم می‌شود)"
else
  ok "دامنه از env: ${BASE_DOMAIN}"
fi

BASE_DOMAIN="${BASE_DOMAIN%,}"
PANEL_DOMAIN="panel.${BASE_DOMAIN}"
GIT_DOMAIN="git.${BASE_DOMAIN}"
APPS_DOMAIN="apps.${BASE_DOMAIN}"

# TLS: برای دامنه واقعی روشن، برای nip.io خاموش (محدودیت گواهی)
# حالت پیش‌فرض نصب = nip.io + HTTP؛ دامنه واقعی و HTTPS را ویزارد وب فعال می‌کند
case "$BASE_DOMAIN" in
  *.nip.io) TLS_ENABLED="false"; warn "دامنه nip.io است؛ HTTPS خاموش شد (بعداً از پنل وب با دامنه واقعی فعال کنید)." ;;
  *)        TLS_ENABLED="true" ;;
esac

ACME_EMAIL="${PARASTOO_ACME_EMAIL:-admin@${BASE_DOMAIN}}"
TRAEFIK_ENTRYPOINTS="web"; TRAEFIK_TLS="false"
if [ "$TLS_ENABLED" = "true" ]; then TRAEFIK_ENTRYPOINTS="web,websecure"; TRAEFIK_TLS="true"; fi

GITEA_ROOT_URL_SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && GITEA_ROOT_URL_SCHEME="https"

# ---------- ۳) رمزها و فایل .env ----------
say "تولید رمزهای امن + توکن نصب..."
gen() { openssl rand -hex 24; }
# توکن یک‌بارمصرف نصب: فقط همین‌جا چاپ می‌شود و گیتِ ویزارد وب است
SETUP_TOKEN="$(openssl rand -hex 6 | sed -E 's/(.{4})(.{4})(.{4})/\1-\2-\3/')"

if [ -f .env ]; then
  warn ".env موجود است؛ رمزهای قبلی حفظ می‌شوند."
  set -a; . ./.env; set +a
fi

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(gen)}"
GITEA_DB_PASSWORD="${GITEA_DB_PASSWORD:-$(gen)}"
GITEA_SECRET_KEY="${GITEA_SECRET_KEY:-$(gen)}"
PARASTOO_SECRET="${PARASTOO_SECRET:-$(gen)}"
GITEA_ADMIN_USER="${GITEA_ADMIN_USER:-parastoo-system}"
GITEA_ADMIN_EMAIL="${GITEA_ADMIN_EMAIL:-system@${BASE_DOMAIN}}"
GITEA_ADMIN_PASSWORD="${GITEA_ADMIN_PASSWORD:-$(gen)}"
PARASTOO_TZ="${PARASTOO_TZ:-Asia/Tehran}"
PARASTOO_DEBT_LIMIT="${PARASTOO_DEBT_LIMIT:-0}"
PARASTOO_COMMISSION="${PARASTOO_COMMISSION:-10}"
PARASTOO_SETUP_TOKEN="${PARASTOO_SETUP_TOKEN:-$SETUP_TOKEN}"
PARASTOO_PUBLIC_IP="${PARASTOO_PUBLIC_IP:-$PUBLIC_IP}"
# تحریم‌ها: آینه pip/npm برای بیلدهای کاربران (خالی = پیش‌فرض رسمی)
PARASTOO_PIP_INDEX_URL="${PARASTOO_PIP_INDEX_URL:-}"
PARASTOO_NPM_REGISTRY="${PARASTOO_NPM_REGISTRY:-}"

cat > .env <<ENV
# ===== پرستو (ساخته‌شده توسط install.sh) =====
BASE_DOMAIN=${BASE_DOMAIN}
PANEL_DOMAIN=${PANEL_DOMAIN}
GIT_DOMAIN=${GIT_DOMAIN}
APPS_DOMAIN=${APPS_DOMAIN}
TLS_ENABLED=${TLS_ENABLED}
ACME_EMAIL=${ACME_EMAIL}
TRAEFIK_ENTRYPOINTS=${TRAEFIK_ENTRYPOINTS}
TRAEFIK_TLS=${TRAEFIK_TLS}
GITEA_ROOT_URL=${GITEA_ROOT_URL_SCHEME}://${GIT_DOMAIN}/

POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
GITEA_DB_PASSWORD=${GITEA_DB_PASSWORD}
GITEA_SECRET_KEY=${GITEA_SECRET_KEY}
PARASTOO_SECRET=${PARASTOO_SECRET}
GITEA_ADMIN_USER=${GITEA_ADMIN_USER}
GITEA_ADMIN_EMAIL=${GITEA_ADMIN_EMAIL}
GITEA_ADMIN_PASSWORD=${GITEA_ADMIN_PASSWORD}

PARASTOO_TZ=${PARASTOO_TZ}
PARASTOO_DEBT_LIMIT=${PARASTOO_DEBT_LIMIT}
PARASTOO_COMMISSION=${PARASTOO_COMMISSION}
PARASTOO_SETUP_TOKEN=${PARASTOO_SETUP_TOKEN}
PARASTOO_PUBLIC_IP=${PARASTOO_PUBLIC_IP}

# --- تحریم‌ها (اختیاری؛ docs/SANCTIONS.md) ---
# آینه pip برای بیلد پایتون، مثال: https://mirrors.arvancloud.ir/pypi/simple
PARASTOO_PIP_INDEX_URL=${PARASTOO_PIP_INDEX_URL}
# رجیستری npm برای بیلد نود، مثال: https://registry.npmjs.ir
PARASTOO_NPM_REGISTRY=${PARASTOO_NPM_REGISTRY}
ENV
chmod 600 .env
ok "پیکربندی در .env ذخیره شد."

# ---------- ۴) پیکربندی ترافیک ----------
say "ساخت پیکربندی ترافیک..."
mkdir -p config/dynamic backups

REDIRECT_BLOCK=""
if [ "$TLS_ENABLED" = "true" ]; then
  REDIRECT_BLOCK="    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https"
fi

awk -v block="$REDIRECT_BLOCK" -v email="$ACME_EMAIL" '
  { gsub(/__WEB_REDIRECT__/, block); gsub(/__ACME_EMAIL__/, email) }
  { print }
' deploy/templates/traefik.yml.tmpl > config/traefik.yml

[ "$TLS_ENABLED" = "true" ] && ok "ریدایرکت HTTP به HTTPS فعال شد."
chmod +x postgres-init/*.sh

# ---------- ۵) بالا آوردن استک ----------
say "ساخت و اجرای استک کامل (بار اول ممکن است چند دقیقه طول بکشد)..."
if ! docker compose up -d --build; then
  echo ""
  die "استک بالا نیامد. اگر خطا مربوط به pull ایمیج است:
  ۱) بستهٔ آفلاین بسازید: bash scripts/fetch-images.sh (روی سرور خارج) → پوشه offline-images/ را همین‌جا بگذارید → دوباره install.sh
  ۲) یا آینهٔ سالم: PARASTOO_REGISTRY_MIRRORS=\"https://docker.arvancloud.ir\" sudo -E bash install.sh
  راهنمای کامل: docs/SANCTIONS.md"
fi

# ---------- ۶) انتظار برای سلامت هسته ----------
say "انتظار برای راه‌اندازی کامل..."
ATTEMPTS=60
for i in $(seq 1 $ATTEMPTS); do
  if docker compose exec -T core curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
    ok "هسته پرستو بالا آمد."
    break
  fi
  [ "$i" = "$ATTEMPTS" ] && { docker compose logs --tail 30 core; die "هسته پرستو آماده نشد. لاگ‌های بالا را ببینید."; }
  sleep 3
done

# ---------- ۷) نصب CLI پرستو ----------
cp -f bin/parastoo /usr/local/bin/parastoo
chmod +x /usr/local/bin/parastoo
echo "$INSTALL_DIR" > /etc/parastoo-install-path
ok "دستور parastoo نصب شد (status/logs/update/backup)."

SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && SCHEME="https"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  پرستو نصب شد!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  راه‌اندازی سه‌گامی (همه‌اش از مرورگر):"
echo "    ۱) آدرس پنل را باز کنید:"
echo ""
echo -e "       ${BLUE}${SCHEME}://${PANEL_DOMAIN}${NC}"
echo ""
echo -e "    ۲) توکن نصب یک‌بارمصرف (همین‌جا کپی کنید):"
echo ""
echo -e "       ${BLUE}${PARASTOO_SETUP_TOKEN}${NC}"
echo ""
echo "    ۳) حساب مدیر را بسازید و دامنه/HTTPS را در همان ویزارد تنظیم کنید."
echo ""
echo "  اطلاعات سیستمی (در .env هم هست):"
echo "         رمز مدیر گیت: ${GITEA_ADMIN_PASSWORD}"
echo ""
echo "  دستورهای مدیریتی:  parastoo status | logs | update | backup | restart"
echo "  تحریم‌ها و بستهٔ آفلاین:  docs/SANCTIONS.md"
echo ""
