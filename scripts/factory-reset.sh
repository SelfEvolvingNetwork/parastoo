#!/usr/bin/env bash
# =============================================================
#  Parastoo factory reset — hardened, one-shot reset (v0.2.17)
#
#  Official one-liner (command 2 of 2 — the other one is bootstrap.sh):
#    curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/scripts/factory-reset.sh | sudo bash
#
#  Why this script exists (lesson from the field, 2026-11-04):
#  the old `parastoo reset` stopped at a plain `docker compose up -d`
#  with no orphan cleanup, no volume-wipe verification and no health
#  check. An interrupted recreate leaves "<hash>_parastoo-<svc>"
#  containers that block `up` with a name Conflict; the script then
#  reported success while Traefik was the only thing running (everything
#  else 404). This script makes reset ACTUALLY reset — and prove it.
#
#  What it does, in order:
#    0) pre-reset best-effort database dump into backups/
#    1) stops & removes EVERYTHING Parastoo owns: site containers,
#       compose stack, stale recreate orphans, any parastoo-* container
#    2) wipes all parastoo_* volumes and VERIFIES they are gone
#       (a reset that cannot prove the wipe is not a reset)
#    3) removes leftover networks + user-project images
#    4) rebuilds & starts the stack from the on-disk code (up -d --build)
#    5) waits for the core healthcheck, then prints the panel URL,
#       the setup token and the wizard link — or full diagnostics + the
#       exact manual recovery line on failure
#
#  Kept: code, .env, backups/.   Wiped: users, projects, git repos,
#  wallet, sites, build history, all data volumes.
#
#  Automation: append --yes (or PARASTOO_RESET_CONFIRM=yes). The
#  interactive confirmation reads /dev/tty — never stdin — so
#  `curl | bash` is safe (stdin is the script pipe itself).
#
#  2026-11-05 hotfix — curl|bash stdin starvation: `docker compose exec`
#  forwards the caller's stdin to the container even with -T; under
#  `curl | bash` it swallowed the rest of the piped script and the reset
#  died silently right after step 0 (dump ok, then nothing — reproduced
#  twice in the field on a healthy install). Every compose exec in this
#  script now reads stdin from /dev/null.
#
#  English output on purpose — Persian/RTL text breaks over SSH.
# =============================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
mark()  { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN} [ok]${NC} $*"; }
warn()  { echo -e "${YELLOW} [warn]${NC} $*"; }
die()   { echo -e "${RED} error:${NC} $*" >&2; exit 1; }

ORG="${PARASTOO_GH_ORG:-SelfEvolvingNetwork}"
REPO="${PARASTOO_GH_REPO:-parastoo}"
RAW_URL="${PARASTOO_GH_PROXY:-}https://raw.githubusercontent.com/${ORG}/${REPO}/main/scripts/factory-reset.sh"

# ---------- 1) root ----------
[ "$(id -u)" -eq 0 ] || die "Run as root:  curl -fsSL $RAW_URL | sudo bash"

# ---------- 2) confirmation — NEVER reads stdin (curl|bash safe) ----------
want_reset() {
  [ "${PARASTOO_RESET_CONFIRM:-}" = "yes" ] && return 0
  case "${1:-}" in --yes|-y) return 0 ;; esac
  if [ -e /dev/tty ] && [ -r /dev/tty ]; then
    local ans=""
    printf "Type 'reset' to PERMANENTLY wipe ALL Parastoo data: "
    read -r ans < /dev/tty || return 1
    [ "$ans" = "reset" ] && return 0
  fi
  return 1
}
if ! want_reset "$@"; then
  die "aborted — confirmation required: type 'reset' at the prompt, or run with --yes (or PARASTOO_RESET_CONFIRM=yes)"
fi

# ---------- 3) locate the installation ----------
DIR_FILE="/etc/parastoo-install-path"
APP_DIR="$(cat "$DIR_FILE" 2>/dev/null || true)"
[ -n "$APP_DIR" ] || APP_DIR="/opt/parastoo"
[ -f "$APP_DIR/docker-compose.yml" ] || die "Parastoo is not installed at $APP_DIR — install first (command 1):
  curl -fsSL https://raw.githubusercontent.com/${ORG}/${REPO}/main/bootstrap.sh | sudo bash"
cd "$APP_DIR" || exit 1
command -v docker >/dev/null 2>&1 || die "docker not found — run the install command first."
docker compose version >/dev/null 2>&1 || die "docker compose plugin not found — run the install command first."

mkdir -p logs
RUN_LOG="logs/factory-reset-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$RUN_LOG") 2>&1

env_val() {  # read a value from .env WITHOUT sourcing it (robust vs unquoted values)
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env 2>/dev/null | head -n1 | tr -d '\r' \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
          -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//"
}

# ---------- 0) best-effort backup before the wipe ----------
mark "0/5) best-effort pre-reset backup (kept in backups/)"
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'parastoo-postgres'; then
  if docker compose exec -T postgres pg_dump -U parastoo parastoo </dev/null 2>/dev/null | gzip > "backups/pre-reset-db-${TS}.sql.gz" \
     && [ -s "backups/pre-reset-db-${TS}.sql.gz" ]; then
    ok "database dump: backups/pre-reset-db-${TS}.sql.gz"
  else
    rm -f "backups/pre-reset-db-${TS}.sql.gz"
    warn "database dump failed (postgres is probably broken — exactly why you are resetting)"
  fi
else
  warn "postgres container is not running — no pre-reset dump possible"
fi

# ---------- 1/5) teardown: every container Parastoo owns ----------
mark "1/5) stopping and removing everything Parastoo owns"
# site containers live OUTSIDE the compose stack (created via Docker SDK,
# restart=unless-stopped) — compose down never touches them, remove first
docker ps -aq --filter name=parastoo-site- | xargs -r docker rm -f >/dev/null 2>&1 || true
docker compose down -v --remove-orphans || warn "compose down reported an error (continuing — the sweep below finishes the job)"
# stale recreate orphans ("<hash>_parastoo-<svc>") + ANY container still
# claiming a parastoo name or compose project label (belt and braces)
STALE="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '(^[0-9a-f]{12}_parastoo-|^parastoo-)' || true)"
if [ -n "${STALE:-}" ]; then
  # shellcheck disable=SC2086
  docker rm -f $STALE >/dev/null 2>&1 || true
  ok "removed leftover containers: $(echo "$STALE" | tr '\n' ' ')"
fi
BYLABEL="$(docker ps -aq --filter label=com.docker.compose.project=parastoo 2>/dev/null || true)"
if [ -n "${BYLABEL:-}" ]; then
  # shellcheck disable=SC2086
  docker rm -f $BYLABEL >/dev/null 2>&1 || true
fi

# ---------- 2/5) wipe volumes and VERIFY the wipe ----------
mark "2/5) wiping data volumes and verifying the wipe"
VOL_LEFT="$(docker volume ls -q 2>/dev/null | grep -E '^parastoo_' || true)"
if [ -n "${VOL_LEFT:-}" ]; then
  # shellcheck disable=SC2086
  docker volume rm $VOL_LEFT >/dev/null 2>&1 || true
fi
VOL_LEFT="$(docker volume ls -q 2>/dev/null | grep -E '^parastoo_' || true)"
if [ -n "${VOL_LEFT:-}" ]; then
  for v in $VOL_LEFT; do
    BLOCKER="$(docker ps -a --filter volume="$v" --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"
    warn "volume $v still in use by: ${BLOCKER:-unknown container}"
  done
  die "cannot wipe volumes: $VOL_LEFT — remove the blockers above, then re-run the reset."
fi
ok "all parastoo_* volumes are gone (verified)"

# ---------- 3/5) networks + user-project images ----------
mark "3/5) cleaning networks and user-project images"
for n in $(docker network ls --format '{{.Name}}' 2>/dev/null | grep -E '^parastoo-' || true); do
  docker network rm "$n" >/dev/null 2>&1 || true
done
docker images -q --filter label=parastoo.slug | xargs -r docker rmi -f >/dev/null 2>&1 || true
ok "networks cleaned, project images removed"

# ---------- 4/5) rebuild from the on-disk code ----------
mark "4/5) rebuilding the stack (docker compose up -d --build; log: $RUN_LOG.up)"
if ! docker compose up -d --build >"$RUN_LOG.up" 2>&1; then
  echo "----- last 40 lines of $RUN_LOG.up -----"
  tail -n 40 "$RUN_LOG.up" || true
  echo "----------------------------------------"
  die "docker compose failed. Manual recovery:
  cd $APP_DIR && sudo bash scripts/db-reconcile.sh && sudo docker compose up -d --build"
fi

# ---------- 5/5) health gate — reset is done ONLY when the core answers ----------
mark "5/5) waiting for the core to become healthy (up to ~3 min after build)"
OKC=0
for _i in $(seq 1 60); do
  if docker compose exec -T core curl -fsS http://localhost:8000/healthz </dev/null >/dev/null 2>&1; then OKC=1; break; fi
  sleep 3
done
if [ "$OKC" -ne 1 ]; then
  docker compose ps || true
  echo "----- last 40 lines of core logs -----"
  docker compose logs --tail 40 core 2>&1 || true
  die "core did NOT become healthy — the reset is not complete. Manual recovery:
  cd $APP_DIR && sudo bash scripts/db-reconcile.sh && sudo docker compose up -d --build"
fi

# ---------- done ----------
BASE="$(env_val BASE_DOMAIN)"
PANEL="$(env_val PANEL_DOMAIN)"
TLS="$(env_val TLS_ENABLED)"
SCHEME="http"; [ "$TLS" = "true" ] && SCHEME="https"
TOK="$(env_val PARASTOO_SETUP_TOKEN)"
ok "core is healthy — Parastoo is back to factory state."
echo ""
echo "    Panel:       ${SCHEME}://${PANEL:-panel.${BASE:-<unset>}}/"
echo "    Wizard:      ${SCHEME}://${PANEL:-panel.${BASE:-<unset>}}/setup"
echo "    Setup token: ${TOK:-see $APP_DIR/.env (PARASTOO_SETUP_TOKEN)}"
echo "    Kept: code, .env, backups/.   Wiped: users, projects, repos, sites, wallet, history."
echo ""
echo "    Update anytime (command 1 of 2):"
echo "      curl -fsSL https://raw.githubusercontent.com/${ORG}/${REPO}/main/bootstrap.sh | sudo bash"
