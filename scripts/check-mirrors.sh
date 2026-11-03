#!/usr/bin/env bash
# =============================================================
#  پرستو — تست‌کنندهٔ آینه‌های Docker Hub از داخل سرور
#
#  هر آینهٔ کاندید (deploy/mirrors.txt) را با endpoint استاندارد /v2/
#  می‌سنجد: پاسخ 200/301/302/401 یعنی رجیستری سالم است. مرده‌ها و
#  کندتر از حد حذف می‌شوند و بقیه به ترتیب سرعت چاپ می‌شوند.
#
#  مصرف:
#      bash scripts/check-mirrors.sh                 # فقط نمایش
#      bash scripts/check-mirrors.sh --apply PATH    # فقط زنده‌ها را در daemon.json بنویس
#  خروجی چاپی: آدرس آینه‌های زنده، یکی در هر خط (سریع‌ترین اول)
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
MIRRORS_FILE="${PARASTOO_MIRRORS_FILE:-deploy/mirrors.txt}"
TIMEOUT="${PARASTOO_MIRROR_TIMEOUT:-6}"
MAX_LATENCY="${PARASTOO_MIRROR_MAX_LATENCY:-4}"   # ثانیه؛ بالاتر از این = خیلی کند
APPLY=""

[ "${1:-}" = "--apply" ] && { APPLY="${2:?مسیر daemon.json؟}"; }

command -v curl >/dev/null 2>&1 || { echo "خطا: curl نصب نیست." >&2; exit 2; }

RESULTS="$(mktemp)"
trap 'rm -f "$RESULTS"' EXIT

alive() {
  # وضعیت HTTP روی /v2/ (با دنبال‌کردن ریدایرکت) + زمان پاسخ
  local url="$1" code latency
  code="$(curl -sSL -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$url/v2/" 2>/dev/null || echo 000)"
  latency="$(curl -sSL -o /dev/null -w '%{time_total}' --max-time "$TIMEOUT" "$url/v2/" 2>/dev/null || echo 999)"
  case "$code" in
    200|301|302|401) : ;;
    *) return 1 ;;
  esac
  # کندی بیش از حد = عملاً بی‌فایده
  awk -v l="$latency" -v m="$MAX_LATENCY" 'BEGIN{exit !(l+0 <= m+0)}' || return 1
  printf '%.2f %s\n' "$latency" "$url"
}

n_total=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '[:space:]')"
  [ -z "$line" ] && continue
  n_total=$((n_total + 1))
  printf '    در حال تست %-42s ' "$line" >&2
  if alive "$line" >> "$RESULTS"; then
    echo "✔ زنده" >&2
  else
    echo "✘" >&2
  fi
done < "$MIRRORS_FILE"

SORTED="$(sort -n "$RESULTS" | awk '{print $2}')"
N_ALIVE="$(echo -n "$SORTED" | grep -c . || true)"

if [ "$N_ALIVE" -eq 0 ]; then
  echo "هیچ آینهٔ زنده‌ای پیدا نشد." >&2
  exit 3
fi

echo "" >&2
echo "  آینه‌های زنده (سریع‌ترین اول): $N_ALIVE از $n_total" >&2
echo "$SORTED" | sed 's/^/    ✔ /' >&2

if [ -n "$APPLY" ]; then
  # shellcheck disable=SC2086
  python3 scripts/merge-docker-mirrors.py "$APPLY" $SORTED >/dev/null
fi

# خروجی استاندارد: فقط آدرس‌ها (برای مصرف install.sh)
echo "$SORTED"
