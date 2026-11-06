#!/usr/bin/env bash
# =============================================================
#  قانون گواهی ماندگار (v0.3.32) — تست کارکردی scripts/tls-store.sh
#  بدون داکر: با آدرس‌های ساختگی (PARASTOO_TLS_STORE / PARASTOO_TLS_LIVE)
#  سناریو = فرمان مالک: ذخیره در آدرس مشخص، چک قبل از درخواست،
#  هرگز حذف نکردن فروشگاه، امنیت دسترسی‌ها.
# =============================================================
set -u
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
STORE="$T/store"
LIVE_DIR="$T/volume"
LIVE="$LIVE_DIR/acme.json"
export PARASTOO_TLS_STORE="$STORE" PARASTOO_TLS_LIVE="$LIVE"
S="$(cd "$(dirname "$0")/.." && pwd)/scripts/tls-store.sh"
PASS=0; FAIL=0
chk() {  # chk "name" rc
  if [ "$2" -eq 0 ]; then echo "  ✔ $1"; PASS=$((PASS+1)); else echo "  ✘ $1"; FAIL=$((FAIL+1)); fi
}
VALID='{"letsencrypt":{"Account":{"email":"a@b.c"},"certificates":[{"domain":{"main":"panel.example.com"}}]}}'

echo "— ذخیره —"
bash "$S" save >/dev/null 2>&1
chk "save با زندهٔ غایب، بی‌صدا و سالم رد می‌شود (نصب تازه)" $?
mkdir -p "$LIVE_DIR"; printf '%s' "$VALID" > "$LIVE"
bash "$S" save >/dev/null 2>&1 && [ -s "$STORE/acme.json" ]
chk "save گواهی معتبر را در آدرس ثابت می‌گذارد" $?
[ "$(stat -c %a "$STORE/acme.json" 2>/dev/null)" = "600" ] && [ "$(stat -c %a "$STORE" 2>/dev/null)" = "700" ]
chk "دسترسی‌های امن: پوشهٔ 700 و فایل 600" $?
bash "$S" save >/dev/null 2>&1
chk "save تکراری بی‌ضرر است (idempotent)" $?

echo "— بازیابی (چک قبل از درخواست) —"
rm -f "$LIVE"
bash "$S" restore >/dev/null 2>&1 && [ -s "$LIVE" ] && [ "$(stat -c %a "$LIVE")" = "600" ]
chk "گواهی حذف‌شده از فروشگاه برگردانده می‌شود با دسترسی 600" $?
bash "$S" restore >/dev/null 2>&1
chk "بازیابی دوباره (زندهٔ معتبر) = هیچ‌کاره سالم" $?

echo "— قانون: هرگز پوشاندن فروشگاه سالم با فایل خراب —"
printf 'garbage{' > "$LIVE"
bash "$S" save >/dev/null 2>&1
python3 -c "import json; json.load(open('$STORE/acme.json'))" 2>/dev/null
chk "save با زندهٔ خراب، فروشگاه سالم را دست نمی‌زند" $?
rm -f "$LIVE"; printf 'garbage{' > "$LIVE"
bash "$S" restore >/dev/null 2>&1 && cmp -s "$LIVE" "$STORE/acme.json"
chk "restore فایل زندهٔ خراب را با نسخهٔ سالم فروشگاه جایگزین می‌کند" $?

echo "— مرزی‌ها —"
mkdir -p "$T/store2"; printf '{}' > "$T/store2/acme.json"
rm -f "$LIVE"
PARASTOO_TLS_STORE="$T/store2" bash "$S" restore >/dev/null 2>&1
[ $? -eq 1 ]
chk "فروشگاه JSON-خالی ({}) معتبر شمرده نمی‌شود (restore = 1)" $?
printf '{"letsencrypt":{"Account":{"email":"x@y.z"}}}' > "$T/store2/acme.json"
rm -f "$LIVE"
PARASTOO_TLS_STORE="$T/store2" bash "$S" restore >/dev/null 2>&1 && cmp -s "$LIVE" "$T/store2/acme.json"
chk "فروشگاهِ فقط-حساب (بدون گواهی) هم ارزش بازیابی دارد (ثبت‌نام CA تکرار نمی‌شود)" $?
bash "$S" status 2>/dev/null | grep -q "store:"
chk "status گزارش دو طرف را می‌دهد" $?

echo ""
echo "tls-store: $PASS موفق، $FAIL ناموفق"
[ "$FAIL" -eq 0 ]
