#!/usr/bin/env bash
# تست واحد «آمادگی ری‌استارت» (v0.5.27) — بدون نیاز به systemd اجرایی.
# قرارداد: یونیت parastoo-stack.service باید (۱) بعد از docker.service بیاید،
# (۲) compose up --wait بزند، (۳) در شکست دوباره تلاش کند، (۴) install.sh
# آن را نصب و enable کند، (۵) uninstall.sh پاکش کند، (۶) doctor.sh وضعیتش
# را گزارش کند، (۷) جاروی نقطهٔ صفر پل با glob آن را بگیرد.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ch() { if [ "$2" = "0" ]; then PASS=$((PASS+1)); echo "  ✔ $1"; else FAIL=$((FAIL+1)); echo "  ✘ $1"; fi; }

U="$ROOT/deploy/systemd/parastoo-stack.service"
[ -f "$U" ]; ch "یونیت وجود دارد" $?

grep -q '^After=.*docker.service' "$U"; ch "ترتیب: بعد از docker.service" $?
grep -q '^Requires=docker.service' "$U"; ch "وابستگی سخت به docker.service" $?
grep -q 'network-online.target' "$U"; ch "پس از network-online" $?
grep -q 'docker compose up -d --wait' "$U"; ch "همگرایی با compose up --wait" $?
grep -q '^Restart=on-failure' "$U"; ch "تلاش دوباره در شکست" $?
grep -q '__INSTALL_DIR__' "$U"; ch "قالب مسیر نصب (__INSTALL_DIR__)" $?
grep -q 'ConditionPathExists=/etc/parastoo-install-path' "$U"; ch "شرط نصب‌بودن" $?

grep -q 'parastoo-stack.service' "$ROOT/install.sh"; ch "install.sh یونیت را نصب می‌کند" $?
grep -q 'enable parastoo-stack.service' "$ROOT/install.sh"; ch "install.sh یونیت را enable می‌کند" $?
grep -q 'is-enabled docker' "$ROOT/install.sh"; ch "install.sh داکر enable را بلند بررسی می‌کند" $?
grep -q 'parastoo-stack.service' "$ROOT/uninstall.sh"; ch "uninstall.sh یونیت را پاک می‌کند" $?
grep -q 'parastoo-stack.service' "$ROOT/scripts/doctor.sh"; ch "doctor.sh وضعیت یونیت را گزارش می‌کند" $?
grep -q 'parastoo-\*\.service' "$ROOT/../access-agent/access_agent/parastoolifecycle.py" \
  ; ch "جاروی نقطهٔ صفر پل با glob parastoo-*.service یونیت را می‌گیرد" $?

# اعتبارسنجی ساختار یونیت (اگر systemd-analyze موجود باشد) — خطاهای محیطی
# (نبودن docker.service یا باینری docker در ماشین تست) پذیرفته است؛ فقط
# خطای ساختاری یونیت (کلید نامعتبر/پارس) قرمز است.
if command -v systemd-analyze >/dev/null 2>&1; then
  TMPU="$(mktemp /tmp/parastoo-stack.XXXXXX.service)"
  sed "s|__INSTALL_DIR__|$ROOT|g" "$U" > "$TMPU"
  OUT="$(systemd-analyze verify "$TMPU" 2>&1)"
  RC=$?
  echo "$OUT" | grep -qiE 'unknown key|invalid|failed to parse|missing' \
    && STRUCT=1 || STRUCT=0
  if [ "$RC" = "0" ]; then
    ch "systemd-analyze verify سبز است" 0
  elif [ "$STRUCT" = "0" ]; then
    ch "systemd-analyze verify: فقط خطای محیطی (بدون docker در ماشین تست) — ساختار یونیت سالم" 0
  else
    ch "systemd-analyze verify ساختار یونیت را رد کرد" 1
    echo "$OUT" | sed 's/^/    /'
  fi
  rm -f "$TMPU"
else
  echo "  - systemd-analyze در دسترس نیست — رد شد (غیرمسئول)"
fi

echo
echo "# جمع‌بندی: $PASS سبز، $FAIL قرمز"
[ "$FAIL" = "0" ]
