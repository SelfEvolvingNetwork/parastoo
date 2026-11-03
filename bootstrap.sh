#!/usr/bin/env bash
# =============================================================
#  پرستو — بوت‌استرپ از گیت‌هاب: کل نصب با یک دستور، از طریق SSH
#
#    curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash
#
#  این اسکریپت بسته را از گیت‌هاب می‌گیرد (به ترتیب تلاش:
#    ۱) ریلیز zip   ۲) تاربال مخزن (codeload)   ۳) git clone
#  )، خودش بسته را باز می‌کند و پوشهٔ بازشده را به setup.sh می‌دهد —
#  setup.sh آن را در /opt/parastoo نصب و install.sh را اجرا می‌کند
#  (در حالت curl | bash هیچ فایلی روی دیسک نیست؛ همه‌چیز از همین اسکریپت رد می‌شود).
#
#  متغیرهای اختیاری (قبل از دستور بگذارید):
#    PARASTOO_REF=v0.2.2        شاخه یا تگ (پیش‌فرض main) — نسخهٔ پایدار را بدهید
#    PARASTOO_GH_PROXY=URL      پیشوند پروکسی گیت‌هاب برای ایران، مثلاً:
#                               https://gh-proxy.com/
#    PARASTOO_ZIP_NAME=...      نام فایل zip ریلیز (پیش‌فرض: parastoo-v0.2.2.zip)
#    بقیهٔ PARASTOO_* (BASE_DOMAIN و ...) مستقیم به install.sh می‌رسند.
#
#  اگر خودِ مخزن را کلون کرده‌اید، فقط داخل همان پوشه:
#    sudo bash bootstrap.sh
# =============================================================
set -euo pipefail

ORG="${PARASTOO_GH_ORG:-SelfEvolvingNetwork}"
REPO="${PARASTOO_GH_REPO:-parastoo}"
REF="${PARASTOO_REF:-main}"
PROXY="${PARASTOO_GH_PROXY:-}"
ZIP_NAME="${PARASTOO_ZIP_NAME:-parastoo-v0.2.2.zip}"
RAW_URL="${PROXY}https://raw.githubusercontent.com/${ORG}/${REPO}/${REF}/bootstrap.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN} ✔${NC} $*"; }
die()  { echo -e "${RED} خطا:${NC} $*" >&2; exit 1; }

# ---------- ۱) root (سازگار با حالت curl | bash) ----------
is_file_self() {
  local s="${BASH_SOURCE[0]:-}"
  [ -n "$s" ] && [ -f "$s" ] && [ "$s" != "bash" ] && [ "$s" != "sh" ] && [ "$s" != "/dev/stdin" ]
}
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    if is_file_self; then
      exec sudo -E bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
    fi
    # حالت pipe: خود فایل را به‌عنوان root دوباره می‌گیریم و اجرا می‌کنیم
    say "دریافت مجدد اسکریپت با دسترسی root..."
    exec sudo -E bash -c "curl -fsSL '$RAW_URL' | bash"
  fi
  die "با root اجرا کنید:  curl -fsSL $RAW_URL | sudo bash"
fi

# ---------- ۲) اگر پکیج همین‌جاست (کلون دستی / پکیج بازشده) ----------
if [ -f ./install.sh ] && [ -f ./docker-compose.yml ] && [ -f ./setup.sh ]; then
  say "پکیج پرستو همین‌جا پیدا شد؛ اجرای مستقیم..."
  exec bash setup.sh
fi

# ---------- ۳) وابستگی دریافت ----------
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  say "نصب curl..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null
fi
fetch() { # fetch <url> <out>
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 2 --max-time 600 -o "$2" "$1"
  else
    wget -q -O "$2" "$1"
  fi
}

WORK="$(mktemp -d /tmp/parastoo-bootstrap.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---------- ۴) دریافت بسته از گیت‌هاب — سه مسیر ----------
say "دریافت پرستو از گیت‌هاب: ${ORG}/${REPO} (ref: ${REF})"

got_zip() { fetch "${PROXY}https://github.com/${ORG}/${REPO}/releases/latest/download/${ZIP_NAME}" "$WORK/pkg.zip" && [ -s "$WORK/pkg.zip" ]; }
got_tar() { fetch "${PROXY}https://codeload.github.com/${ORG}/${REPO}/tar.gz/${REF}" "$WORK/pkg.tar.gz" && [ -s "$WORK/pkg.tar.gz" ]; }
got_clone() {
  command -v git >/dev/null 2>&1 || {
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1 || return 1
  }
  git clone --depth 1 --branch "$REF" "${PROXY}https://github.com/${ORG}/${REPO}.git" "$WORK/repo" 2>/dev/null
}

MODE=""
if got_zip; then
  MODE="zip"; ok "بستهٔ ریلیز دانلود شد (${ZIP_NAME})."
elif got_tar; then
  MODE="tar"; ok "تاربال مخزن دانلود شد (ref: ${REF})."
elif got_clone; then
  MODE="git"; ok "مخزن کلون شد (ref: ${REF})."
else
  die "دریافت از گیت‌هاب ناموفق بود.
راه‌های جایگزین:
  ۱) پروکسی (ایران):  curl -fsSL ${PROXY}https://gh-proxy.com/https://raw.githubusercontent.com/${ORG}/${REPO}/${REF}/bootstrap.sh | sudo bash
  ۲) دستی:  git clone https://github.com/${ORG}/${REPO}.git && cd ${REPO} && sudo bash setup.sh
  ۳) بستهٔ zip را از صفحهٔ ریلیزها بگیرید و کنار سرور بگذارید:  sudo bash setup.sh parastoo-v0.2.2.zip"
fi

# ---------- ۵) باز کردن بسته و تحویل «پوشه» به setup.sh ----------
# نکتهٔ مهم: در حالت curl | bash هیچ setup.shی در پوشهٔ کاری نیست؛
# بسته را همین‌جا باز می‌کنیم و مسیر پوشهٔ بازشده را به setup.sh داخل
# همان پوشه می‌دهیم (setup.sh آن را در /opt/parastoo نصب می‌کند).
if [ "$MODE" = "git" ]; then
  SRC_DIR="$WORK/repo"
else
  say "باز کردن بسته..."
  EXTRACT_DIR="$WORK/src"
  mkdir -p "$EXTRACT_DIR"
  case "$MODE" in
    zip) if command -v unzip >/dev/null 2>&1; then unzip -q "$WORK/pkg.zip" -d "$EXTRACT_DIR"
         else python3 -m zipfile -e "$WORK/pkg.zip" "$EXTRACT_DIR"; fi ;;
    tar) tar -xzf "$WORK/pkg.tar.gz" -C "$EXTRACT_DIR" ;;
  esac
  if [ -f "$EXTRACT_DIR/install.sh" ]; then
    SRC_DIR="$EXTRACT_DIR"
  else
    SRC_DIR="$(find "$EXTRACT_DIR" -maxdepth 2 -name install.sh -printf '%h\n' 2>/dev/null | head -n1 || true)"
  fi
fi
[ -n "${SRC_DIR:-}" ] && [ -f "$SRC_DIR/setup.sh" ] && [ -f "$SRC_DIR/install.sh" ] \
  || die "بستهٔ معتبر نیست (setup.sh/install.sh پیدا نشد)."
ok "بستهٔ سالم؛ نصب‌کننده از ${SRC_DIR} اجرا می‌شود..."
exec bash "$SRC_DIR/setup.sh" "$SRC_DIR"
