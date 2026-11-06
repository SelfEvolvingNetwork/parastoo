#!/usr/bin/env bash
# Publish a Parastoo release to GitHub: push main + create release + upload zip asset + verify.
# Usage:   GITHUB_TOKEN=ghp_xxx bash scripts/github_publish.sh [path/to/parastoo-vX.Y.Z.zip]
# Token needs: Contents = Read and write on the repo (fine-grained) or "repo" scope (classic).
set -euo pipefail

REPO="${PARASTOO_GH_REPO:-SelfEvolvingNetwork/parastoo}"
BRANCH="main"
TOKEN="${GITHUB_TOKEN:-${PARASTOO_GH_TOKEN:-}}"
[ -n "$TOKEN" ] || { echo "ERROR: set GITHUB_TOKEN=<token>"; exit 1; }
# never leak the token into error output or git config
GIT_URL="https://x-access-token:${TOKEN}@github.com/${REPO}.git"
API="https://api.github.com"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json")

# version comes from the source of truth: core/app/config.py
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="$(sed -n 's/^ *VERSION: *str *= *"\([^"]*\)".*/\1/p' "$HERE/core/app/config.py" | head -1)"
[ -n "$VER" ] || { echo "ERROR: VERSION not found in core/app/config.py"; exit 1; }
ZIP="${1:-$HERE/../parastoo-v${VER}.zip}"
[ -f "$ZIP" ] || { echo "ERROR: zip not found: $ZIP"; exit 1; }
echo "repo=$REPO  version=$VER  zip=$ZIP"

# 1) auth smoke test
code=$(curl -s -o /tmp/gh_repo.json -w '%{http_code}' "${AUTH[@]}" "$API/repos/$REPO")
[ "$code" = "200" ] || { echo "ERROR: token rejected by $API/repos/$REPO (HTTP $code)"; exit 1; }
echo "[1/5] token OK"

# 2) sync repo tree with the local package (remote history is preserved)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 5 --branch "$BRANCH" "$GIT_URL" "$TMP/repo" 2>/dev/null
rsync -a --delete --exclude '.git' --exclude '.env' --exclude 'backups/' \
      --exclude 'offline-images/' --exclude '__pycache__/' --exclude '*.pyc' \
      --exclude '.secrets/' \
      "$HERE/" "$TMP/repo/"
cd "$TMP/repo"
if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
  echo "[2/5] repo already identical to the local package"
else
  git add -A
  git -c user.name="parastoo-release" -c user.email="noreply@users.noreply.github.com" \
      commit -m "پرستو v${VER} — انتشار"
  git push origin "$BRANCH" 2>/dev/null
  echo "[2/5] pushed $BRANCH"
fi

# 3) create or update the release (idempotent)
REL_JSON="$TMP/release.json"
cat > "$REL_JSON" <<EOF
{"tag_name":"v${VER}","target_commitish":"${BRANCH}","name":"v${VER}",
 "body":"## تغییرات از v0.2.10 تا v0.3.23 (خلاصهٔ مهم)\n\n- **هویت سرور و دامنه**: ویزارد ۳ مرحله‌ای (توکن نصب ← ادمین ← دامنه/HTTPS)، فعال‌سازی خودگردان دامنهٔ معلق (ورکر هر ۶۰ث، سقف ۷۲س)، نیم‌سرور اختصاصی PowerDNS با خودترمیم رکوردها، پروب HTTPS سرتاسری در ویزارد.\n- **پایداری میدانی (آزمون روی سرور واقعی، 2026-11-06)**:\n  - رجیستری داخلی گیتا با دامنهٔ کهنه جا می‌ماند → همهٔ بیلدها در push با «404 page not found» می‌میرند؛ حالا ورکر heal هر ۱۲۰ث و خودِ apply_domain گیتا را با دامنهٔ فعال بازسازی می‌کنند و .env هاست همگام می‌ماند.\n  - دستورهای push صفحهٔ پروژه http:// و فرض «مخزن خالی» داشتند؛ اکنون کلون‌محور با https پویا و توضیح کامیت اولیهٔ seed شده.\n  - ترافیک پس از یک شکست ACME دیگر تلاش نمی‌کرد و سایت منتشرشده بی‌گواهی می‌ماند؛ حالا فاز دوم ورکر heal گواهی سایت‌های بی‌گواهی را (با گارد DNS و محدودیت زمانی) ترمیم می‌کند و انتشار پیام صادقانهٔ «گواهی در جریان است» می‌دهد.\n- **تجربهٔ کاربری**: پیام‌های فارسی خطاها (۴۰۴/۴۰۳/۴۰۵/۴۲۹)، دوجهته‌سازی کامل، چیدمان موبایل، پیام مخصوص شکست روشن/خاموش‌کردن سایت، خروج ویزارد به داشبورد.\n- **عملیات**: ریست کارخانه («parastoo reset»)، uninstall کامل (کانتینرهای سایت + ایمیج‌ها)، بکاپ شامل گواهی‌ها، خودآزمایی استقرار در log/update.log، ۳۹۵ چک smoke.\n\nنصب: curl -fsSL https://raw.githubusercontent.com/${REPO}/main/bootstrap.sh | sudo bash"}
EOF
code=$(curl -s -o "$TMP/rel_out.json" -w '%{http_code}' "${AUTH[@]}" \
        -d @"$REL_JSON" "$API/repos/$REPO/releases")
if [ "$code" = "201" ]; then
  echo "[3/5] release v$VER created"
else
  # PATCH has no /tags/ route — resolve the release id first
  REL_ID="$(curl -s "${AUTH[@]}" "$API/repos/$REPO/releases/tags/v${VER}" \
            | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))' 2>/dev/null || true)"
  [ -n "$REL_ID" ] || { echo "ERROR: release v$VER exists but its id was not found"; exit 1; }
  code=$(curl -s -o "$TMP/rel_out.json" -w '%{http_code}' "${AUTH[@]}" \
          -X PATCH -d @"$REL_JSON" "$API/repos/$REPO/releases/$REL_ID")
  [ "$code" = "200" ] || { echo "ERROR: release patch failed (HTTP $code)"; cat "$TMP/rel_out.json"; exit 1; }
  echo "[3/5] release v$VER updated"
fi

# 4) upload the zip asset (delete stale asset with the same name first)
UP_BASE="$(sed -n 's/.*"upload_url": *"\([^?{]*\).*/\1/p' "$TMP/rel_out.json")"
[ -n "$UP_BASE" ] || { echo "ERROR: no upload_url"; exit 1; }
# delete stale assets with the same name (re-publish case), then upload
while IFS= read -r aid; do
  curl -s -o /dev/null -X DELETE "${AUTH[@]}" "$API/repos/$REPO/releases/assets/$aid"
done < <(curl -s "${AUTH[@]}" "$API/repos/$REPO/releases/tags/v${VER}" \
         | python3 -c "import sys,json
try:
    for a in json.load(sys.stdin).get('assets',[]):
        print(a['id'])
except Exception:
    pass")
code=$(curl -s -o "$TMP/up.json" -w '%{http_code}' "${AUTH[@]}" \
        -H "Content-Type: application/zip" --data-binary @"$ZIP" \
        "$UP_BASE?name=parastoo-v${VER}.zip")
[ "$code" = "201" ] || { echo "ERROR: asset upload failed (HTTP $code)"; cat "$TMP/up.json"; exit 1; }
echo "[4/5] asset uploaded"

# 5) public verification
sleep 2
ok=0
curl -s -o /dev/null -w 'release page:   %{http_code}\n' "https://github.com/$REPO/releases/tag/v$VER" | tee /tmp/pub.txt
curl -s -o /dev/null -w 'raw bootstrap:  %{http_code}\n' "https://raw.githubusercontent.com/$REPO/main/bootstrap.sh" | tee -a /tmp/pub.txt
curl -s "https://raw.githubusercontent.com/$REPO/main/core/app/config.py" | grep -q "VERSION: str = \"$VER\"" \
  && echo 'VERSION on main: OK' | tee -a /tmp/pub.txt || echo 'VERSION on main: MISMATCH' | tee -a /tmp/pub.txt
curl -sL -o /dev/null -w 'zip asset:      %{http_code}\n' "https://github.com/$REPO/releases/download/v$VER/parastoo-v${VER}.zip" | tee -a /tmp/pub.txt
ok=$(grep -cE '(200|OK)$' /tmp/pub.txt || true)
[ "$ok" = "4" ] || { echo "PUBLISH INCOMPLETE — see statuses above"; exit 1; }
echo "[5/5] all four public checks passed — v$VER is live"
