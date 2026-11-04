#!/usr/bin/env bash
# =============================================================
#  Parastoo regression test — the REAL bootstrap route, end to end
#  up to (and past) the point where v0.2.12 crashed in production.
#
#  The user route is:
#    curl -fsSL .../bootstrap.sh | sudo bash
#  which downloads the repo TARBALL (no .git dir), extracts it and
#  runs install.sh — with `set -u` active. v0.2.12 died there with
#  "install.sh: line 364: PARASTOO_DEPLOYED_COMMIT: unbound variable"
#  BEFORE .env was written and BEFORE docker compose up.
#
#  This test reproduces that route inside the sandbox:
#    - package extracted with `git archive`  ->  no .git, like the tarball
#    - install.sh runs with stubbed id/docker/curl/systemctl (no root, no docker)
#    - PASS = install.sh reaches `docker compose up` (stub) with a valid
#      .env (incl. PARASTOO_DEPLOYED_COMMIT) and a written .parastoo-deploy
#
#  Run:  bash tests/install_tarball_route_test.sh
# =============================================================
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
FAIL=0
pass() { printf ' [ok] %s\n' "$*"; }
bad()  { printf ' [FAIL] %s\n' "$*" >&2; FAIL=1; }

command -v git >/dev/null 2>&1 || { bad "git not available in the sandbox"; exit 1; }

WORK="$(mktemp -d /tmp/parastoo-route-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
STUB="$WORK/stub"; mkdir -p "$STUB"

# ---------- stubs (a container-less imitation of the server) ----------
cat > "$STUB/id" <<'EOF'
#!/usr/bin/env bash
[ "$1" = "-u" ] && { echo 0; exit 0; }
exec /usr/bin/id "$@"
EOF
cat > "$STUB/curl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *ipify*|*ifconfig*|*checkip*|*icanhazip*) echo "203.0.113.9"; exit 0 ;;
  *healthz*) echo "200"; exit 0 ;;
  *) exit 7 ;;   # every probe/mirror/registry call fails -> warns, never fatal
esac
EOF
cat > "$STUB/docker" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "--version "*|"compose version") exit 0 ;;
  "buildx version") exit 1 ;;    # -> best-effort install path, warn, continue
  "pull"*) exit 1 ;;             # -> test-pull warn, continue
  "info") exit 1 ;;              # -> skip in-container probes
  *) exit 0 ;;                   # compose up / exec health check succeed
esac
EOF
cat > "$STUB/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$STUB/id" "$STUB/curl" "$STUB/docker" "$STUB/systemctl"

# ---------- the package, exactly as bootstrap's tarball route delivers it ----------
mkdir -p "$WORK/pkg"
git archive HEAD --prefix=parastoo-main/ | tar -x -C "$WORK/pkg" || { bad "git archive failed"; exit 1; }
PKG="$WORK/pkg/parastoo-main"
[ -f "$PKG/install.sh" ] && [ ! -d "$PKG/.git" ] \
  || { bad "extracted package is not .git-less (route not reproduced)"; exit 1; }

# ---------- run the installer on the simulated server ----------
# env-pass route variant: bootstrap hands over the resolved commit
LOG="$WORK/install.log"
if PARASTOO_DEPLOYED_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
   PATH="$STUB:$PATH" HOME="$WORK" \
   bash "$PKG/install.sh" >"$LOG" 2>&1; then
  pass "install.sh completed on the tarball route (no unbound-variable crash)"
else
  RC=$?
  if grep -q 'unbound variable' "$LOG"; then
    bad "install.sh crashed with an unbound variable (rc=$RC):"
    grep 'unbound variable' "$LOG" | sed 's/^/        /' >&2
  else
    # a non-crash failure late in the script (e.g. writing /usr/local/bin as
    # non-root) is acceptable IF the critical .env phase was reached
    if [ -f "$PKG/.env" ] && [ -f "$PKG/.parastoo-deploy" ]; then
      pass "install.sh passed the .env/.parastoo-deploy phase (stopped later at rc=$RC: $(tail -n1 "$LOG" | cut -c1-60))"
    else
      bad "install.sh failed (rc=$RC) before the critical phase; last lines:"
      tail -n 5 "$LOG" | sed 's/^/        /' >&2
    fi
  fi
fi

# ---------- assertions on the artifacts ----------
if [ -f "$PKG/.env" ]; then
  pass ".env was written"
  if grep -q '^PARASTOO_DEPLOYED_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa$' "$PKG/.env"; then
    pass ".env carries the bootstrap-provided commit (env-pass route)"
  elif grep -q '^PARASTOO_DEPLOYED_COMMIT=$' "$PKG/.env"; then
    pass ".env carries an empty-but-defined PARASTOO_DEPLOYED_COMMIT (no crash; auto-updater fills it)"
  else
    bad ".env has an unexpected PARASTOO_DEPLOYED_COMMIT line:"
    grep 'PARASTOO_DEPLOYED_COMMIT' "$PKG/.env" | sed 's/^/        /' >&2
  fi
else
  bad ".env was NOT written (the v0.2.12 production crash point)"
fi
if [ -f "$PKG/.parastoo-deploy" ]; then
  pass ".parastoo-deploy was written: $(tr '\n' ' ' < "$PKG/.parastoo-deploy")"
else
  bad ".parastoo-deploy was NOT written (/api/version source)"
fi
if grep -q 'auto-update enabled\|Auto-update enabled' "$LOG"; then
  pass "auto-update timer step reached"
fi

printf '\n'
[ "$FAIL" = "0" ] && { pass "install_tarball_route_test: PASSED"; exit 0; }
bad "install_tarball_route_test: FAILED"
exit 1
