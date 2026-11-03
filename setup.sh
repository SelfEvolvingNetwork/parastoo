#!/usr/bin/env bash
# =============================================================
#  پرستو — نصب با یک دستور، از صفر تا پنل
#
#  حالت‌های استفاده:
#
#    ۱) فایل zip کنار همین اسکریپت است (یا در پوشهٔ فعلی):
#         sudo bash setup.sh
#
#    ۲) مسیر یا نام فایل zip:
#         sudo bash setup.sh parastoo-v0.2.1.zip
#
#    ۳) دانلود مستقیم از لینک (zip یا tar.gz):
#         sudo bash setup.sh https://example.com/parastoo-v0.2.1.zip
#
#    ۳ب) پوشهٔ پکیج از قبل بازشده (bootstrap.sh هم همین‌طور صدا می‌زند):
#         sudo bash setup.sh /مسیر/پوشهٔ/پرستو
#
#    ★ راه پیشنهادی — بوت‌استرپ خودکار از گیت‌هاب (bootstrap.sh):
#         curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash
#
#    ۴) حالت کلاسیک curl | bash (اسکریپت را جایی هاست کنید):
#         curl -fsSL https://example.com/setup.sh | bash -s -- https://example.com/parastoo-v0.2.1.zip
#
#    ۵) اگر خودتان پکیج را از قبل باز کرده‌اید، همین‌جا اجرایش کنید —
#       مستقیم install.sh همان پوشه را نصب می‌کند:
#         sudo bash setup.sh
#
#  نصب مجدد = آپدیت: /opt/parastoo با کد جدید به‌روز می‌شود؛
#  .env، پیکربندی ترافیک، بستهٔ آفلاین و پشتیبان‌ها دست‌نخورده می‌مانند
#  و داده‌ها (ولیوم‌های داکر) حفظ می‌شوند.
# =============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()   { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN} ✔${NC} $*"; }
warn()  { echo -e "${YELLOW} !${NC} $*"; }
die()   { echo -e "${RED} خطا:${NC} $*" >&2; exit 1; }

INSTALL_ROOT="/opt/parastoo"
SRC="${1:-}"
SELF="${BASH_SOURCE[0]:-}"

[ "$(id -u)" -eq 0 ] || die "با sudo اجرا کنید:  sudo bash setup.sh"

# ---------- ۱) تعیین منبع: zip محلی، URL، یا پکیج از قبل بازشده ----------
is_url()  { case "$1" in http://*|https://*) return 0 ;; *) return 1 ;; esac; }
self_dir() { [ -f "$SELF" ] && dirname "$(readlink -f "$SELF")" || echo ""; }

# اگر اسکریپت داخل پکیجِ بازشده است، همان‌جا install.sh کافی است
DIRECT_DIR=""
if [ -f "$SELF" ]; then
  d="$(self_dir)"
  [ -f "$d/docker-compose.yml" ] && [ -f "$d/install.sh" ] && DIRECT_DIR="$d"
fi

latest_zip() {  # جدیدترین parastoo*.zip از بین الگوهای داده‌شده
  local best="" best_t=0 t pat f
  for pat in "$@"; do
    for f in $pat; do          # گسترش عمدی الگوی glob
      [ -f "$f" ] || continue
      t="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
      if [ "$t" -ge "$best_t" ]; then best="$f"; best_t="$t"; fi
    done
  done
  echo "$best"
}

ZIP_PATH=""
if [ -n "$SRC" ]; then
  if is_url "$SRC"; then
    MODE="url"
  elif [ -d "$SRC" ] && [ -f "$SRC/install.sh" ]; then
    SRC_DIR="$(readlink -f "$SRC")"; MODE="dir"      # پوشهٔ پکیج بازشده
  elif [ -f "$SRC" ]; then
    ZIP_PATH="$(readlink -f "$SRC")"; MODE="zip"
  else
    die "فایل یا پوشه پیدا نشد: $SRC"
  fi
elif [ -n "$DIRECT_DIR" ]; then
  MODE="direct"
else
  sd="$(self_dir)"
  ZIP_PATH="$(latest_zip "${sd:+$sd/parastoo*.zip}" "$PWD/parastoo*.zip" 2>/dev/null || true)"
  [ -n "$ZIP_PATH" ] && MODE="zip" || { MODE="none"; }
fi

# ---------- ۲) اجرای مستقیم روی پکیج بازشده ----------
if [ "${MODE:-}" = "direct" ]; then
  say "پکیج پرستو همین‌جاست؛ اجرای نصب‌کننده..."
  cd "$DIRECT_DIR"
  exec bash install.sh
fi

if [ "${MODE:-}" = "none" ]; then
  die "هیچ منبعی پیدا نشد.
استفاده:
  sudo bash setup.sh <نام فایل zip>
  sudo bash setup.sh https://example.com/parastoo-v0.2.1.zip
  sudo bash setup.sh /مسیر/پوشهٔ/پکیج/بازشده
یا این اسکریپت را کنار فایل zip بگذارید و فقط:  sudo bash setup.sh"
fi

# ---------- ۳) وابستگی‌ها: curl / unzip (با فال‌بک python3) ----------
if [ "${MODE:-}" != "dir" ]; then
  say "بررسی وابستگی‌ها..."
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
  fi
  if ! command -v unzip >/dev/null 2>&1; then
    apt-get install -y -qq unzip >/dev/null 2>&1 || true
  fi
  EXTRACTOR="unzip"
  command -v unzip >/dev/null 2>&1 || EXTRACTOR="python3"   # اوبونتو همیشه python3 دارد
fi

WORK="$(mktemp -d /tmp/parastoo-setup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---------- ۴) دریافت بسته (حالت پوشه رد می‌شود) ----------
if [ "${MODE:-}" = "dir" ]; then
  WORK_SRC="$SRC_DIR"
  ok "پوشهٔ پکیج: $WORK_SRC"
else
  # پسوند حفظ می‌شود تا تاربال گیت‌هاب هم پشتیبانی شود (pkg.zip یا pkg.tar.gz)
  case "${MODE}:${SRC:-}" in
    url:*.tar.gz|url:*.tgz) PKG="$WORK/pkg.tar.gz" ;;
    url:*)                  PKG="$WORK/pkg.zip" ;;
    *)                      PKG="$WORK/pkg.tar.gz"; case "$ZIP_PATH" in *.tar.gz|*.tgz) ;; *) PKG="$WORK/pkg.zip" ;; esac ;;
  esac

  if [ "$MODE" = "url" ]; then
    say "دانلود بسته از: $SRC"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --retry 2 --max-time 900 -o "$PKG" "$SRC" \
        || die "دانلود ناموفق بود. لینک را چک کنید یا فایل را دستی روی سرور بگذارید."
    else
      wget -q -O "$PKG" "$SRC" || die "دانلود ناموفق بود."
    fi
    ZIP_PATH="$PKG"
  else
    say "بسته: $ZIP_PATH"
    cp -f "$ZIP_PATH" "$PKG"
  fi

  # ---------- ۵) استخراج و نرمال‌سازی ساختار ----------
  say "استخراج بسته..."
  mkdir -p "$WORK/src"        # tar برای -C به پوشهٔ موجود نیاز دارد
  case "$ZIP_PATH" in
    *.tar.gz|*.tgz)
      tar -xzf "$ZIP_PATH" -C "$WORK/src"        # تاربال گیت‌هاب (codeload)
      ;;
    *)
      if [ "$EXTRACTOR" = "unzip" ]; then
        unzip -q "$ZIP_PATH" -d "$WORK/src"
      else
        python3 -m zipfile -e "$ZIP_PATH" "$WORK/src"
      fi
      ;;
  esac
  # بسته ممکن است ریشه parastoo/ داشته باشد یا مستقیم فایل‌ها را داشته باشد
  if [ ! -f "$WORK/src/docker-compose.yml" ]; then
    inner="$(find "$WORK/src" -maxdepth 2 -name docker-compose.yml -printf '%h\n' 2>/dev/null | head -n1 || true)"
    [ -n "$inner" ] && WORK_SRC="$inner" || die "بستهٔ معتبر نیست (docker-compose.yml پیدا نشد)."
  else
    WORK_SRC="$WORK/src"
  fi
fi
[ -f "$WORK_SRC/install.sh" ] || die "بستهٔ معتبر نیست (install.sh پیدا نشد)."
ok "بستهٔ سالم: $(grep -m1 -o 'v0\.[0-9.]*' "$WORK_SRC/README.md" 2>/dev/null || echo 'پرستو')"

# ---------- ۶) نصب در /opt/parastoo (یا آپدیت امن) ----------
if [ -f "$INSTALL_ROOT/.env" ]; then
  say "نصب قبلی پیدا شد؛ آپدیت امن (کد جدید، بدون دست‌زدن به .env و داده‌ها)..."
  # فقط پوشه‌های کد جایگزین می‌شوند؛ .env/config/backups/offline-images می‌مانند
  rm -rf "$INSTALL_ROOT/core" "$INSTALL_ROOT/bin" "$INSTALL_ROOT/deploy" \
         "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/postgres-init" "$INSTALL_ROOT/docs"
  cp -a "$WORK_SRC/." "$INSTALL_ROOT/"
  ok "کد پرستو به‌روز شد."
else
  say "نصب تازه در $INSTALL_ROOT ..."
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  rm -rf "$INSTALL_ROOT"
  cp -a "$WORK_SRC" "$INSTALL_ROOT"
fi
chmod +x "$INSTALL_ROOT/install.sh" "$INSTALL_ROOT/uninstall.sh" \
         "$INSTALL_ROOT/scripts/"*.sh "$INSTALL_ROOT/bin/parastoo" 2>/dev/null || true

# ---------- ۷) اجرای نصب‌کنندهٔ اصلی (داکر، آینه‌ها، استک، CLI) ----------
cd "$INSTALL_ROOT"
exec bash install.sh
