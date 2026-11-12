#!/usr/bin/env bash
# =============================================================
#  Parastoo regression test — .env heredoc of install.sh must
#  survive `set -u` (nounset) on EVERY install route.
#
#  Real-world failure (v0.2.12, fixed in v0.2.13): bootstrap.sh
#  installs from a tarball (no .git dir), PARASTOO_DEPLOYED_COMMIT
#  stayed unset and the `.env` heredoc killed install.sh with
#  "unbound variable" — BEFORE docker compose up / .parastoo-deploy.
#
#  What this test does:
#    A) dynamic audit — every ${VAR} referenced inside the .env
#       heredoc must be unconditionally assigned earlier in
#       install.sh (or have a ${VAR:-default} guard right there);
#    B) runtime repro — the heredoc is actually expanded under
#       `set -u` twice: tarball route (no git, no inherited env)
#       and env-pass route (bootstrap hands over the commit).
#
#  Run:  bash tests/install_env_test.sh
# =============================================================
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
FAIL=0

say()  { printf '  %s\n' "$*"; }
pass() { printf ' [ok] %s\n' "$*"; }
bad()  { printf ' [FAIL] %s\n' "$*" >&2; FAIL=1; }

[ -f install.sh ] || { bad "install.sh not found (run from repo root)"; exit 1; }

# ---------- locate the .env heredoc ----------
HEREDOC_START="$(grep -n '^cat > \.env <<ENV$' install.sh | head -n1 | cut -d: -f1)"
[ -n "$HEREDOC_START" ] || { bad "could not locate 'cat > .env <<ENV' in install.sh"; exit 1; }
HEREDOC_END="$(awk -v s="$HEREDOC_START" 'NR>s && /^ENV$/{print NR; exit}' install.sh)"
[ -n "$HEREDOC_END" ] || { bad "could not locate the end of the .env heredoc"; exit 1; }
BODY="$(sed -n "$((HEREDOC_START + 1)),$((HEREDOC_END - 1))p" install.sh)"

# ---------- A) dynamic audit ----------
# variable names referenced in the heredoc body
REFERENCED="$(printf '%s\n' "$BODY" | grep -oE '\$\{?[A-Za-z_][A-Za-z0-9_]*' \
             | sed 's/^\${\?//' | sort -u)"
# BARE = referenced without a ${VAR:-default} guard — those MUST be guaranteed
BARE="$(printf '%s\n' "$BODY" | grep -oE '\$\{?[A-Za-z_][A-Za-z0-9_]*(:-)?' \
       | sed 's/^\${\?//; s/:-$//' | sort -u)"
# PRESTRIP = install.sh up to the heredoc, with single/double-quoted strings and
# comments removed — words like `if`/`for` inside comments or messages must not
# confuse the block-depth analyzer below.
PRESTRIP="$(head -n "$HEREDOC_START" install.sh \
  | sed "s/'[^']*'//g" \
  | sed 's/"[^"]*"/QQ/g' \
  | sed 's/[[:space:]]#.*$//; s/^#.*$//')"
ASSIGNED="$(printf '%s\n' "$PRESTRIP" \
            | tr ';|&(){}' '       ' \
            | tr -s '[:space:]' '\n' \
            | grep -E '^(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' \
            | sed 's/^export[[:space:]]*//; s/=.*//' | sort -u)"
# GUARANTEED for `set -u` = assigned at conditional-depth 0 (a case branch with
# a '*' catch-all counts; an if/for/while body does not) OR via a self-guard
# 'VAR="${VAR:-...}"'. Depth is counted word-wise on COMMENT- AND STRING-FREE
# lines, so single-line 'if ...; then VAR=x; fi' and multi-line blocks both work.
GUARANTEED="$(printf '%s\n' "$PRESTRIP" \
  | awk '
      { n = split($0, w)
        for (i = 1; i <= n; i++) {
          t = w[i]
          if (t == "if" || t == "for" || t == "while" || t == "until") d++
          else if (t == "fi" || t == "done") { if (d > 0) d-- }
          else if (t ~ /^(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=/) {
            name = t; sub(/^export[[:space:]]+/, "", name); sub(/=.*/, "", name)
            if (d == 0) print name
          }
        }
      }' \
  | sort -u)"
SELF_GUARD="$(head -n "$HEREDOC_START" install.sh \
  | grep -oE '(^|[[:space:];)])[A-Za-z_][A-Za-z0-9_]*="\$\{[A-Za-z_][A-Za-z0-9_]*:-' \
  | sed 's/.*[[:space:];)]//; s/="\${.*//' | sort -u)"
GUARANTEED="$(printf '%s\n' "$GUARANTEED" "$SELF_GUARD" | sed '/^$/d' | sort -u)"
# rule 1: every referenced name must be assigned somewhere before the heredoc
MISSING="$(comm -23 <(printf '%s\n' "$REFERENCED") <(printf '%s\n' "$ASSIGNED"))"
# rule 2: every BARE reference must be guaranteed (this catches the original
# bug: PARASTOO_DEPLOYED_COMMIT was assigned only inside 'if [ -d .git ]')
MISSING="$(printf '%s\n' "$MISSING" "$(comm -23 <(printf '%s\n' "$BARE") <(printf '%s\n' "$GUARANTEED"))" \
          | sed '/^$/d' | sort -u)"
if [ -n "$MISSING" ]; then
  bad "heredoc references variables not guaranteed before it (unbound under set -u):"
  printf '%s\n' "$MISSING" | sed 's/^/        /' >&2
  bad "add 'VAR=\"\${VAR:-}\"' (or a real default) for each one above"
else
  pass "audit: all $(printf '%s\n' "$REFERENCED" | wc -l) heredoc variables are guaranteed before the heredoc"
fi

# ---------- B) runtime repro, route 1: tarball (no .git, no inherited env) ----------
OUT="$(env -i PATH="$PATH" HOME=/tmp bash -c '
  set -euo pipefail
  # simulate the tail of install.sh exactly at the tarball route:
  # git absent, no .env sourced, nothing inherited — then expand the heredoc
  command -v git >/dev/null 2>&1 || true
  PARASTOO_DEPLOYED_AT="2026-01-01 00:00:00"
  PARASTOO_DEPLOYED_COMMIT=""
  if [ -z "$PARASTOO_DEPLOYED_COMMIT" ] && command -v git >/dev/null 2>&1 && [ -d .git ]; then
    PARASTOO_DEPLOYED_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
  fi
  BASE_DOMAIN=example.nip.io PANEL_DOMAIN=panel.example.nip.io
  GIT_DOMAIN=git.example.nip.io APPS_DOMAIN=apps.example.nip.io
  TLS_ENABLED=false ACME_EMAIL=admin@example.nip.io
  TRAEFIK_ENTRYPOINTS=web TRAEFIK_TLS=false GITEA_ROOT_URL_SCHEME=http
  POSTGRES_PASSWORD=x GITEA_DB_PASSWORD=x GITEA_SECRET_KEY=x PARASTOO_SECRET=x
  GITEA_ADMIN_USER=admin GITEA_ADMIN_EMAIL=a@b.c GITEA_ADMIN_PASSWORD=x
  PARASTOO_TZ=Asia/Tehran PARASTOO_DEBT_LIMIT=0 PARASTOO_COMMISSION=10
  PARASTOO_SETUP_TOKEN=abcd-efgh-ijkl PARASTOO_PUBLIC_IP=203.0.113.9
  PARASTOO_AUTO_UPDATE=true PARASTOO_AUTO_UPDATE_INTERVAL=120
  PARASTOO_UPDATE_CHANNEL=main PARASTOO_GH_PROXY=
  PARASTOO_DEV_TOKEN=
  PARASTOO_PIP_INDEX_URL= PARASTOO_NPM_REGISTRY=
  cat <<ENV
PARASTOO_DEPLOYED_COMMIT=${PARASTOO_DEPLOYED_COMMIT}
PARASTOO_DEPLOYED_AT=${PARASTOO_DEPLOYED_AT}
ENV
' 2>&1)"
if [ "$(printf '%s' "$OUT" | grep -c 'unbound variable')" != "0" ]; then
  bad "route tarball: heredoc expansion hit an unbound variable:"
  printf '%s\n' "$OUT" | sed 's/^/        /' >&2
else
  pass "route tarball: guarded reference expands cleanly under set -u"
fi

# ---------- B) runtime repro, route 2: bootstrap env-pass ----------
OUT2="$(env -i PATH="$PATH" HOME=/tmp PARASTOO_DEPLOYED_COMMIT=deadbeefdeadbeefdeadbeefdeadbeefdeadbeef bash -c '
  set -euo pipefail
  PARASTOO_DEPLOYED_AT="2026-01-01 00:00:00"
  PARASTOO_DEPLOYED_COMMIT="${PARASTOO_DEPLOYED_COMMIT:-}"
  cat <<ENV
commit=${PARASTOO_DEPLOYED_COMMIT}
ENV
' 2>&1)"
case "$OUT2" in
  "commit=deadbeef"*) pass "route env-pass: bootstrap-provided commit reaches the .env heredoc" ;;
  *unbound*)           bad "route env-pass: unbound variable:"; printf '%s\n' "$OUT2" >&2 ;;
  *)                   bad "route env-pass: unexpected output: $OUT2" ;;
esac

# ---------- C) the fix must actually be present ----------
if grep -q '^PARASTOO_DEPLOYED_COMMIT="\${PARASTOO_DEPLOYED_COMMIT:-}"$' install.sh; then
  pass "install.sh contains the always-present default for PARASTOO_DEPLOYED_COMMIT"
else
  bad "the guarded default line is missing from install.sh (regression)"
fi

# ---------- C2) domain precedence: explicit env must beat the OLD .env ----------
# Field incident 2026-11-04 (v0.2.18 and earlier): install.sh sourced the old
# .env AFTER deriving the domain identity, so a stale PANEL_DOMAIN inside .env
# clobbered the PARASTOO_BASE_DOMAIN the user passed NOW and won over the
# Traefik label + printed URL. Guard: the explicit-intent capture must exist,
# and the LAST derivation of PANEL_DOMAIN must come AFTER the .env sourcing.
SRC_LINE="$(grep -n '\. \./\.env' install.sh | head -n1 | cut -d: -f1)"
DER_LINE="$(grep -n '^PANEL_DOMAIN="panel\.\${BASE_DOMAIN}"$' install.sh | tail -n1 | cut -d: -f1)"
CAP_LINE="$(grep -n '^_EXPLICIT_BASE="\${PARASTOO_BASE_DOMAIN:-}"$' install.sh | head -n1 | cut -d: -f1)"
REASS_LINE="$(grep -n '^  BASE_DOMAIN="\$_EXPLICIT_BASE"$' install.sh | head -n1 | cut -d: -f1)"
if [ -n "$CAP_LINE" ]; then
  pass "explicit domain intent captured (_EXPLICIT_BASE) at line $CAP_LINE"
else
  bad "install.sh does not capture _EXPLICIT_BASE before sourcing .env"
fi
if [ -n "$SRC_LINE" ] && [ -n "$DER_LINE" ] && [ "$DER_LINE" -gt "$SRC_LINE" ]; then
  pass "PANEL_DOMAIN is (re)derived AFTER the old .env is sourced (line $DER_LINE > $SRC_LINE)"
else
  bad "PANEL_DOMAIN is derived before/without the .env sourcing — stale .env would win again"
fi
if [ -n "$REASS_LINE" ]; then
  pass "explicit PARASTOO_BASE_DOMAIN is re-asserted after sourcing (line $REASS_LINE)"
else
  bad "the BASE_DOMAIN='\$_EXPLICIT_BASE' re-assertion is missing from install.sh"
fi

# ---------- D) shell syntax of the touched scripts ----------
for f in install.sh bootstrap.sh setup.sh scripts/auto-update.sh; do
  if bash -n "$f" 2>/dev/null; then pass "bash -n: $f"; else bad "bash -n: $f has syntax errors"; fi
done

printf '\n'
if [ "$FAIL" = "0" ]; then
  pass "install_env_test: all checks passed"
  exit 0
fi
bad "install_env_test: FAILED"
exit 1
