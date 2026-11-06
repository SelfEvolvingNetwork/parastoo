#!/usr/bin/env bash
# Publish a Parastoo release to GitHub: push main + build zip from the pushed tree + create release + upload + verify.
# Usage:   GITHUB_TOKEN=ghp_xxx bash scripts/github_publish.sh
# v0.3.28: the zip is ALWAYS built from exactly what was just pushed, and
# carries its own build stamp (.release-commit). Field lesson: a pre-built zip
# lagging main shipped v0.3.27 without the balance-guard commit — fresh
# installs silently ran old code while /api/version claimed the newest sha.
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
ZIP="$(cd "$HERE/.." && pwd)/parastoo-v${VER}.zip"
echo "repo=$REPO  version=$VER  zip=$ZIP (built from the pushed tree by this script)"

# 1) auth smoke test
code=$(curl -s -o /tmp/gh_repo.json -w '%{http_code}' "${AUTH[@]}" "$API/repos/$REPO")
if [ "$code" = "200" ]; then
  echo "[1/5] token OK"
else
  echo "ERROR: token rejected by $API/repos/$REPO (HTTP $code)"; exit 1
fi

# 2) sync repo tree with the local package (remote history is preserved)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 5 --branch "$BRANCH" "$GIT_URL" "$TMP/repo" 2>/dev/null
rsync -a --delete --exclude '.git' --exclude '.env' --exclude 'backups/' \
      --exclude 'offline-images/' --exclude '__pycache__/' --exclude '*.pyc' \
      --exclude '.secrets/' \
      "$HERE/" "$TMP/repo/"
# v0.3.27: the bootstrap default zip name must always match the release being
# cut — the smoke check "نام zip پیش‌فرض بوت‌استرپ هم‌نسخهٔ جاری است" caught a
# forgotten manual update (2026-11-06). Stamp it here so no release ships a
# stale default again.
sed -i -E "s/parastoo-v[0-9]+\.[0-9]+\.[0-9]+\.zip/parastoo-v${VER}.zip/g" "$TMP/repo/bootstrap.sh"
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

# 2.5) build the release zip from EXACTLY the tree that was just pushed
# (v0.3.28): a pre-built zip lagging main shipped v0.3.27 without the
# balance-guard commit — fresh installs silently ran old code. The zip also
# carries .release-commit (its own build sha) so bootstrap records the truth.
PUSHED_SHA="$(git -C "$TMP/repo" rev-parse HEAD)"
echo "[2.5/5] building zip from pushed tree @ ${PUSHED_SHA:0:12}..."
STAGE="$TMP/zipstage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
rsync -a --delete --exclude '.git' "$TMP/repo/" "$STAGE/"
printf '%s\n' "$PUSHED_SHA" > "$STAGE/.release-commit"
rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  (cd "$STAGE" && zip -qr "$ZIP" .)
else
  (cd "$STAGE" && python3 -m zipfile -c "$ZIP" .)
fi
[ -s "$ZIP" ] || { echo "ERROR: zip build produced nothing: $ZIP"; exit 1; }
if ! python3 - "$ZIP" <<'PYEOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
names = z.namelist()
stamped = any(n.endswith(".release-commit") and not n.startswith("__MACOSX") for n in names)
sha = ""
if stamped:
    n = [x for x in names if x.endswith(".release-commit") and not x.startswith("__MACOSX")][0]
    sha = z.read(n).decode().strip()[:12]
print(f"zip entries: {len(names)}  .release-commit: {sha or 'MISSING'}")
raise SystemExit(0 if stamped and len(names) > 50 else 1)
PYEOF
then
  echo "ERROR: built zip is invalid (no .release-commit or empty)"; exit 1
fi

# 3) create or update the release (idempotent)
REL_JSON="$TMP/release.json"
cat > "$REL_JSON" <<EOF
{"tag_name":"v${VER}","target_commitish":"${BRANCH}","name":"v${VER}",
 "body":"## v0.3.28 — صداقت خط انتشار و گارد موجودی در نصب تازه (تست نصب از صفر، 2026-11-06)\n\n- **نصب تازه کد قدیمی اجرا می‌کرد و ادعای جدیدترین کامیت را داشت**: bootstrap ریلیز zip را می‌گرفت ولی استمپ commit را از HEAD زندهٔ main می‌نوشت؛ zip ریلیز v0.3.27 پیش از کامیت گارد موجودی بریده شده بود و نصب تازه بی‌سروصدا کد قدیمی می‌گرفت (میدان: انتشار سایت با کیف صفر مجاز شد و ورکر بیلینگ سایت را در همان دقیقه کشت). حالا: برای main تاربال مخزن اولویت دارد (همیشه == main) و zip فقط برای تگ پین‌شده منبع اول است؛ zip ریلیز هم استمپ build خودش (.release-commit) را حمل می‌کند و bootstrap همان را ثبت می‌کند — نه sha اصلی.\n- **github_publish.sh حالا zip را از همان درختی که push کرده می‌سازد** (نه zip از پیش ساخته‌شده) و استمپ .release-commit را داخلش می‌گذارد؛ صحت استمپ پیش از آپلود راستی‌آزمایی می‌شود.\n- **گارد موجودی در هر سه مسیر روشن‌کردن سایت (v0.3.27) حالا واقعاً به نصب‌ها می‌رسد**: انتشار با کیف کمتر از یک ساعت پلن → flash صادقانه no_balance؛ پایان «منتشر شد سبز و ۶۰ ثانیه بعد مرگ بی‌صدا».\n- **خودترمیمی زون DNS در فعال‌سازی دامنه (پایان lame delegation نصب تازه)**: اگر زون پایه روی PDNS خودمان نباشد و NS دامنه به این سرور اشاره کند، apply_domain خودش زون base را provision می‌کند (پنل/گیت/سایت‌ها زنده)؛ میدان: پس از نصب تازه، رکوردهای وایلدکارد REFUSED می‌دادند و ویزارد بدون دخالت دستی بن‌بست می‌شد.\n- از v0.3.27: پروب آینهٔ PyPI/npm به دانلود واقعی + استمپ خودکار zip-name بوت‌استرپ.\n\nنصب: curl -fsSL https://raw.githubusercontent.com/${REPO}/main/bootstrap.sh | sudo bash"}"}
EOF
# field guard (v0.3.26): backticks inside an unquoted heredoc run as command
# substitution and silently eat parts of the release notes — strip them all
sed -i 's/`//g' "$REL_JSON"
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
