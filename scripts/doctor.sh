#!/usr/bin/env bash
# =============================================================
#  Parastoo doctor — collect a sanitized diagnostic report
#
#  Why: the installer runs on servers nobody else can see. When
#  something is broken, run this and SEND THE WHOLE OUTPUT to
#  your assistant/support — it compresses an hour of questions
#  into one paste.
#
#  Secrets (passwords / tokens / keys) are replaced with ********
#  — the report is safe to share.
#
#  Usage:   sudo parastoo doctor
#       or  sudo bash /opt/parastoo/scripts/doctor.sh
#  Output:  printed to the terminal AND saved under backups/
# =============================================================
set -uo pipefail

APP_DIR="/opt/parastoo"
[ -f /etc/parastoo-install-path ] && APP_DIR="$(cat /etc/parastoo-install-path)"
# if the registered install dir is gone, fall back to this script's own package
if [ ! -d "$APP_DIR" ]; then
  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
fi
cd "$APP_DIR" 2>/dev/null || true

TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$APP_DIR/backups" 2>/dev/null || true
REPORT="$APP_DIR/backups/doctor-report-${TS}.txt"
# last resort: a read-only install dir must never break the report
if ! touch "$REPORT" 2>/dev/null; then
  REPORT="/tmp/parastoo-doctor-report-${TS}.txt"
fi

VER="$(grep -m1 -o 'v[0-9][0-9.]*' "$APP_DIR/README.md" 2>/dev/null || echo 'unknown')"
PHASE="$(cat "$APP_DIR/.install-state" 2>/dev/null || echo 'no installer state (installed before v0.2.7, or install never ran)')"
PANEL="$(grep -E '^PANEL_DOMAIN=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)"
TLS="$(grep -E '^TLS_ENABLED=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)"
PUBIP="$(grep -E '^PARASTOO_PUBLIC_IP=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2-)"
SCHEME="http"; [ "$TLS" = "true" ] && SCHEME="https"

probe() {  # probe <label> <url> — any HTTP answer counts as reachable
  local code
  code="$(timeout 10 curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$2" 2>/dev/null || true)"
  if [ -n "$code" ] && [ "$code" != "000" ]; then
    echo "  $1: HTTP $code (reachable)"
  else
    echo "  $1: BLOCKED / no answer"
  fi
}

{
  echo "============================================================"
  echo " Parastoo doctor report — $(date '+%F %T %Z')"
  echo " (secrets are redacted with ******** — safe to share)"
  echo "============================================================"

  echo ""
  echo "[install]"
  echo "  version: $VER"
  echo "  install dir: $APP_DIR"
  echo "  last completed installer phase: $PHASE"
  echo "  panel: ${SCHEME}://${PANEL:-?}"

  echo ""
  echo "[system]"
  echo "  kernel: $(uname -r)   uptime: $(uptime -p 2>/dev/null || echo '?')"
  echo "  disk /: $(df -h / 2>/dev/null | tail -1 | awk '{print $3" used / "$2" total / "$5" full"}')"
  echo "  memory: $(free -m 2>/dev/null | awk 'NR==2{print $3" MB used / "$2" MB total"}')"
  echo "  server public ip: ${PUBIP:-unknown}"

  echo ""
  echo "[docker]"
  docker --version 2>/dev/null || echo "  docker: NOT INSTALLED"
  docker compose version 2>/dev/null || echo "  docker compose: NOT AVAILABLE"
  echo "  active registry mirrors (from the daemon):"
  docker info 2>/dev/null | sed -n '/Registry Mirrors:/,+6p' | sed 's/^/  /' || true
  grep -q 'Registry Mirrors' <(docker info 2>/dev/null) || echo "    none configured"

  echo ""
  echo "[containers]"
  if ! docker compose ps 2>/dev/null; then
    echo "  docker compose ps failed — run this report with sudo"
  fi
  for c in parastoo-core parastoo-traefik parastoo-gitea parastoo-postgres; do
    docker inspect -f '  {{.Name}}  state={{.State.Status}}  health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}  restarts={{.RestartCount}}' "$c" 2>/dev/null \
      || echo "  $c  NOT CREATED"
  done

  echo ""
  echo "[images]"
  docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' 2>/dev/null \
    | grep -E 'parastoo|gitea|postgres|traefik|alpine' || echo "  (no stack images found)"

  echo ""
  echo "[health]"
  if docker compose exec -T core curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
    echo "  core healthz: OK"
  else
    echo "  core healthz: FAILING (see core logs below)"
  fi
  if [ -n "$PANEL" ]; then
    echo "  panel via traefik: HTTP $(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 -H "Host: $PANEL" http://127.0.0.1/ 2>/dev/null || echo 'no answer')"
  fi
  echo "  ports 80/443: $(ss -tlnp 2>/dev/null | grep -cE ':(80|443)\s') listening"

  echo ""
  echo "[reachability — from this server]"
  probe "docker hub (registry-1.docker.io) " "https://registry-1.docker.io/v2/"
  probe "pypi.org (index)                  " "https://pypi.org/simple/"
  probe "files.pythonhosted.org (downloads)" "https://files.pythonhosted.org/"
  probe "registry.npmjs.org                " "https://registry.npmjs.org/-/ping"
  probe "raw.githubusercontent.com         " "https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh"

  echo ""
  echo "[logs — last 6 lines each]"
  for svc in core traefik gitea postgres; do
    echo "  --- $svc ---"
    docker compose logs --tail 6 "$svc" 2>/dev/null | sed 's/^/  /' || echo "  (no logs)"
  done

  echo ""
  echo "[config — secrets redacted]"
  if [ -f "$APP_DIR/.env" ]; then
    sed -E 's/^([A-Z_]*(PASSWORD|SECRET|TOKEN)[A-Z_]*)=.*/\1=********/' "$APP_DIR/.env" | sed 's/^/  /'
  else
    echo "  .env NOT FOUND (install never completed?)"
  fi

  echo ""
  echo "============================================================"
  echo " end of report"
  echo "============================================================"
} > "$REPORT" 2>&1

cat "$REPORT"
echo ""
echo "Report saved: $REPORT"
echo "Send the WHOLE report above when asking for help."
