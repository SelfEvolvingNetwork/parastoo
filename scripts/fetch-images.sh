#!/usr/bin/env bash
# =============================================================
#  پرستو — سازندهٔ بستهٔ آفلاین ایمیج‌ها (راه‌حل مطمئن تحریم‌ها)
#
#  کِی استفاده کنیم؟ وقتی سرور ایران به هیچ آینه‌ای هم دسترسی ندارد
#  یا می‌خواهید نصب ۱۰۰٪ بدون اینترنت خارجی باشد.
#
#  چطور؟ این اسکریپت را روی «یک ماشین بدون تحریم» اجرا کنید
#  (لپ‌تاپ شخصی با تغییر IP، سرور خارج، یا GitHub Actions):
#
#      docker version           # داکر لازم است
#      bash scripts/fetch-images.sh
#
#  خروجی: offline-images/parastoo-images.tar.gz
#  آن پوشه را کنار پکیج پرستو روی سرور ایران منتقل کنید و install.sh
#  را دوباره اجرا کنید — ایمیج‌ها از بستهٔ محلی بارگذاری می‌شوند،
#  هیچ pull از Docker Hub انجام نمی‌شود.
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
IMAGES_FILE="deploy/images.txt"
OUT_DIR="${1:-offline-images}"

command -v docker >/dev/null 2>&1 || { echo "خطا: داکر نصب نیست."; exit 1; }

# --- خواندن لیست ایمیج‌ها (خط‌های غیر خالی و غیر کامنت) ---
LIST=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '[:space:]')"
  [ -z "$line" ] && continue
  LIST+=("$line")
done < "$IMAGES_FILE"

[ ${#LIST[@]} -gt 0 ] || { echo "خطا: $IMAGES_FILE خالی است."; exit 1; }

echo "==> pull ${#LIST[@]} ایمیج برای پلتفرم linux/amd64 ..."
for img in "${LIST[@]}"; do
  echo "    docker pull --platform linux/amd64 $img"
  docker pull --platform linux/amd64 "$img"
done

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/parastoo-images.tar.gz"
echo "==> ساخت آرشیو: $ARCHIVE"
docker save "${LIST[@]}" | gzip > "$ARCHIVE"

echo ""
echo "  انجام شد: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
echo "  قدم بعدی: پوشهٔ «$OUT_DIR» را کنار پکیج پرستو روی سرور منتقل کنید (scp) و sudo bash install.sh را دوباره اجرا کنید."
