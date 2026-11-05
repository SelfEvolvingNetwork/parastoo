#!/usr/bin/env bash
# =============================================================
#  Parastoo — single-command installer, sanctions-aware (Iran)
#  Usage:   sudo bash install.sh
#  Result:  a complete single-server PaaS (panel + git + hosting)
#
#  Anti-sanctions layers (automatic, zero questions):
#    1) Docker install: get.docker.com <- on sanctions -> Ubuntu repos
#    2) Images:         Docker Hub     <- on sanctions -> Iranian mirrors in daemon.json
#    3) Fully offline:  offline-images/ folder next to the package -> docker load (no pull at all)
#    4) Builds:         PyPI/npm       <- on sanctions -> Iranian mirrors written into .env
#  Details: docs/SANCTIONS.md
#
#  NOTE: all installer output is English on purpose — Persian/RTL
#  text gets mangled over SSH. The web panel itself stays Persian.
# =============================================================
set -euo pipefail

# ---------- colors ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()   { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN} [ok]${NC} $*"; }
warn()  { echo -e "${YELLOW} [warn]${NC} $*"; }
die()   { echo -e "${RED} error:${NC} $*" >&2; echo -e "        diagnostic report to share: ${BLUE}bash ${INSTALL_DIR:-/opt/parastoo}/scripts/doctor.sh${NC}" >&2; exit 1; }

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$INSTALL_DIR"

[ "$(id -u)" -eq 0 ] || die "This script must run with sudo."
[ -f docker-compose.yml ] || die "install.sh must run from the Parastoo package root."

# ---------- 0) if interrupted (Ctrl+C / SSH drop), say exactly how to finish ----------
# Real-world failure this fixes: the only silent phase of the installer is the
# PyPI/npm reachability probe (up to ~1 min, no output). Quitting there means
# `docker compose up` never ran -> images are cached but zero containers exist.
on_interrupt() {
  echo "" >&2
  warn "Installation was interrupted before it finished."
  [ -f "$INSTALL_DIR/.install-state" ] \
    && echo -e "   Last completed phase: ${BLUE}$(cat "$INSTALL_DIR/.install-state" 2>/dev/null)${NC}" >&2
  echo -e "   Finish the stack manually:  ${BLUE}cd $INSTALL_DIR && sudo bash scripts/db-reconcile.sh && sudo docker compose up -d --build${NC}" >&2
  echo    "   (re-running the same install command is also safe — nothing is lost)" >&2
  echo -e "   Diagnostic report to share: ${BLUE}bash $INSTALL_DIR/scripts/doctor.sh${NC}" >&2
  exit 130
}
trap on_interrupt INT TERM HUP

# progress marker: the last phase written here is what `parastoo doctor`
# reports — so an interrupted install can be diagnosed without access to the server
mark() { echo "$(date '+%F %T')  $*" > "$INSTALL_DIR/.install-state" 2>/dev/null || true; }

# ---------- 1) prerequisites ----------
mark "1) prerequisites"
say "Checking prerequisites..."
if ! command -v curl >/dev/null 2>&1; then
  say "Installing curl..."
  apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
fi

# git is a runtime dependency of the auto-updater (git ls-remote); without it
# auto-update silently never deploys (v0.2.13)
if ! command -v git >/dev/null 2>&1; then
  say "Installing git (needed by the auto-updater)..."
  if apt-get update -qq >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1; then
    ok "git installed."
  else
    warn "git could not be installed; auto-update needs it (git ls-remote) — install git manually."
  fi
fi

# ---------- 1a) Docker install — with sanctions fallback ----------
install_docker_from_ubuntu() {
  # Ubuntu's own repos (ir.archive.ubuntu.com and local mirrors) are not sanctioned
  say "Installing Docker from Ubuntu repositories..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 docker-buildx >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose >/dev/null 2>&1 || return 1
  systemctl enable --now docker >/dev/null 2>&1 || true
  return 0
}

if ! command -v docker >/dev/null 2>&1; then
  mark "1a) installing docker"
  say "Docker not found; installing automatically..."
  # route 1: official script (best on non-sanctioned servers — also installs the compose plugin)
  if timeout 90 curl -fsSL --max-time 60 https://get.docker.com -o /tmp/get-docker.sh 2>/dev/null \
     && sh /tmp/get-docker.sh >/dev/null 2>&1; then
    ok "Docker installed from the official script."
  else
    # route 2: Ubuntu repos — always reachable from inside Iran
    warn "get.docker.com is not reachable (sanctions); falling back to Ubuntu repositories"
    install_docker_from_ubuntu \
      || die "Docker install failed. Manual fix: docs/SANCTIONS.md, section 'Docker install'"
    ok "Docker installed from Ubuntu repositories."
  fi
else
  ok "Docker present: $(docker --version)"
fi
systemctl enable --now docker >/dev/null 2>&1 || true

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin not found. Install docker-compose-v2 (docs/SANCTIONS.md)."
fi

# buildx: newer compose emits a Bake warning without it; best-effort install
# (this also covers the case where Docker was installed before Parastoo)
if ! docker buildx version >/dev/null 2>&1; then
  say "docker buildx is missing; trying the Ubuntu/official package (best effort)..."
  apt-get update -qq >/dev/null 2>&1 || true
  if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx >/dev/null 2>&1 \
     || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx-plugin >/dev/null 2>&1; then
    ok "docker-buildx installed."
  else
    warn "docker-buildx not installable from the repos; continuing without it."
  fi
fi

# ---------- 1b) offline image bundle (optional — the most reliable route) ----------
shopt -s nullglob
OFFLINE_TARS=(offline-images/*.tar offline-images/*.tar.gz parastoo-images*.tar.gz)
shopt -u nullglob
OFFLINE_LOADED=0
if [ ${#OFFLINE_TARS[@]} -gt 0 ]; then
  say "Offline image bundle found; loading directly (no Docker Hub contact)..."
  for tar in "${OFFLINE_TARS[@]}"; do
    if docker load -i "$tar"; then ok "Loaded: $tar"; OFFLINE_LOADED=1; else warn "Load failed: $tar"; fi
  done
fi

# ---------- 1c) registry mirrors — bypassing Docker Hub sanctions ----------
# Iranian services (registry.docker.ir, ArvanCloud, NovinCloud, ...) only
# need daemon.json config; candidate list: deploy/mirrors.txt
# This section: detect sanctions <- test mirror liveness from this very
# server <- write healthy ones ordered by speed <- verify with a real pull.

hub_reachable() {
  # standard registry endpoint: 401 means reachable (fast; no pull)
  [ "$(timeout 12 curl -fsS -o /dev/null -w '%{http_code}' --max-time 8 https://registry-1.docker.io/v2/ 2>/dev/null)" = "401" ]
}

apply_mirrors() {
  local mirrors="$1"
  if command -v python3 >/dev/null 2>&1 && [ -f scripts/merge-docker-mirrors.py ]; then
    # shellcheck disable=SC2086
    python3 scripts/merge-docker-mirrors.py /etc/docker/daemon.json $mirrors || return 1
    systemctl restart docker >/dev/null 2>&1 || service docker restart >/dev/null 2>&1 || true
    sleep 2
    return 0
  fi
  return 1
}

mark "1c) registry mirrors"
if [ -n "${PARASTOO_REGISTRY_MIRRORS:-}" ]; then
  say "Configuring mirrors from env (PARASTOO_REGISTRY_MIRRORS)..."
  apply_mirrors "$PARASTOO_REGISTRY_MIRRORS" \
    && ok "Mirrors configured." \
    || die "Failed to set mirrors; edit /etc/docker/daemon.json manually (docs/SANCTIONS.md)."
elif hub_reachable; then
  ok "Docker Hub is reachable (no mirrors needed)."
else
  say "Docker Hub is not responding (sanctions); testing Iranian mirrors from this server..."
  if [ "$OFFLINE_LOADED" = "1" ]; then
    # required images came from the offline bundle; mirrors only help future builds
    ALIVE="$(bash scripts/check-mirrors.sh 2>/dev/null || true)" || true
    if [ -n "$ALIVE" ]; then
      apply_mirrors "$ALIVE" && ok "Mirrors also configured for future builds." || true
    else
      warn "No live mirror found — continuing with the offline bundle; for future builds see docs/SANCTIONS.md."
    fi
  else
    ALIVE="$(bash scripts/check-mirrors.sh || true)"
    if [ -n "$ALIVE" ]; then
      apply_mirrors "$ALIVE" \
        && ok "$(echo "$ALIVE" | wc -l) live mirror(s) active in /etc/docker/daemon.json (fastest first)." \
        || warn "Failed to write mirrors — docs/SANCTIONS.md"
    else
      warn "No live mirror found; update the list: deploy/mirrors.txt or use PARASTOO_REGISTRY_MIRRORS"
      warn "Without mirrors, image pulls fail — reliable route: offline bundle (docs/SANCTIONS.md, layer 3)"
    fi
    # final verification: a small real pull (same image the backup command needs)
    if timeout 90 docker pull -q alpine:3.20 >/dev/null 2>&1; then
      ok "Final check: pull through the mirror works."
    else
      warn "Test pull also failed. Reliable route: bash scripts/fetch-images.sh on an unsanctioned machine -> offline-images/ folder -> run install.sh again"
    fi
  fi
fi

# ---------- 1d) layer 4 — PyPI/npm mirrors (core build and user builds) ----------
# Iran blocks the PyPI index (pypi.org) and the download host
# (files.pythonhosted.org) INDEPENDENTLY: a real install failed with
# "Temporary failure in name resolution" on files.pythonhosted.org while
# pypi.org answered fine — so both hosts are probed. When docker is usable,
# the probe also runs inside an alpine container, because the build uses
# the daemon's DNS which can differ from the host's. Any HTTP response
# counts as reachable (the download host answers 404 on /); only DNS
# failure / connection reset counts as blocked.
host_http_ok() {  # any HTTP status = alive; 000 = DNS failure / reset / timeout
  local code
  code="$(timeout 12 curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$1" 2>/dev/null || true)"
  [ -n "$code" ] && [ "$code" != "000" ]
}
docker_probe_ok() {  # same reachability check inside a throwaway container (build-faithful DNS)
  docker run --rm alpine:3.20 sh -c "wget -S -T 8 -O /dev/null '$1' 2>&1 | grep -q 'HTTP/'" >/dev/null 2>&1
}
probe_full() {  # probe_full <label> <url>; 0 = usable for builds (host + container DNS)
  printf '      probing %-44s' "$1"
  if ! host_http_ok "$2"; then echo "BLOCKED/timeout"; return 1; fi
  if docker info >/dev/null 2>&1 && docker image inspect alpine:3.20 >/dev/null 2>&1; then
    printf '\n      %-66s' "(same probe inside a container — the build uses the daemon DNS)"
    if docker_probe_ok "$2"; then echo "ok"; return 0; fi
    echo "BLOCKED"
    warn "$1: reachable from the host but NOT from containers — a mirror will be used for builds."
    return 1
  fi
  echo "ok (host only — docker absent, container re-check skipped)"; return 0
}

mark "1d) PyPI/npm probes"
# Real-world report: this used to be the ONLY silent phase (up to ~1 min) and
# users read the silence as a hang — every host and mirror now prints its result.
say "Checking PyPI/npm reachability (one line per host; a blocked host costs ~8 s)..."
DETECTED_PIP_MIRROR=""; DETECTED_NPM_MIRROR=""
PYPI_OK=no; FILES_OK=no; NPM_OK=no
if [ -z "${PARASTOO_PIP_INDEX_URL:-}" ]; then
  probe_full "pypi.org (package index)" "https://pypi.org/simple/"                  && PYPI_OK=yes
  probe_full "files.pythonhosted.org (downloads)" "https://files.pythonhosted.org/" && FILES_OK=yes
  if [ "$PYPI_OK" = yes ] && [ "$FILES_OK" = yes ]; then
    ok "PyPI fully reachable (index + download host). No mirror needed."
  else
    say "Probing Python package mirrors from this server..."
    for m in \
      https://package-mirror.liara.ir/repository/pypi/simple \
      https://mirror.abrha.net/repository/pypi/simple \
      https://mirror-pypi.runflare.com/simple \
      https://mirror.kargadan.ir/repository/pypi-group/simple \
      https://mirrors.arvancloud.ir/pypi/simple \
      https://pypi.tuna.tsinghua.edu.cn/simple \
      https://mirrors.aliyun.com/pypi/simple; do
      # -L follows redirects; trailing slash matters for some mirrors; 200 strictly required
      printf '      testing %-58s' "${m%/}/"
      if [ "$(timeout 12 curl -fsSL -o /dev/null -w '%{http_code}' --max-time 8 "${m%/}/" 2>/dev/null)" = "200" ]; then
        echo "ok"
        DETECTED_PIP_MIRROR="$m"; ok "Live pip mirror: $m (will be written to .env)"; break
      fi
      echo "fail"
    done
    [ -n "$DETECTED_PIP_MIRROR" ] || warn "No live pip mirror found; the core build may fail — docs/SANCTIONS.md, layer 4"
  fi
fi
if [ -z "${PARASTOO_NPM_REGISTRY:-}" ]; then
  probe_full "registry.npmjs.org" "https://registry.npmjs.org/-/ping" && NPM_OK=yes
  if [ "$NPM_OK" = yes ]; then
    ok "Official npm is reachable. No mirror needed."
  else
    say "Probing npm mirrors from this server..."
    for m in https://registry.npmjs.ir https://registry.npmmirror.com; do
      printf '      testing %-58s' "$m/-/ping"
      if [ "$(timeout 12 curl -fsSL -o /dev/null -w '%{http_code}' --max-time 8 "$m/-/ping" 2>/dev/null)" = "200" ]; then
        echo "ok"
        DETECTED_NPM_MIRROR="$m"; ok "Live npm mirror: $m (will be written to .env)"; break
      fi
      echo "fail"
    done
    [ -n "$DETECTED_NPM_MIRROR" ] || warn "No live npm mirror found; node builds may fail."
  fi
fi

# ---------- 2) base domain — zero questions ----------
# Install always starts with a temporary domain (nip.io) so it asks
# nothing; the real domain and HTTPS are set later from the web panel
# (domain wizard) using the one-time setup token.
BASE_DOMAIN="${PARASTOO_BASE_DOMAIN:-}"
mark "2) base domain"
# Explicit PARASTOO_PUBLIC_IP wins. On NAT/CGNAT/tunnel servers the OUTBOUND
# IP (what ipify/ifconfig.me see) differs from the INBOUND IP (what users hit)
# — the auto-generated nip.io domain would then point at the wrong host
# (field incident 2026-11-04: panel printed an unreachable address).
PUBLIC_IP="${PARASTOO_PUBLIC_IP:-}"
if [ -z "$PUBLIC_IP" ]; then
  say "Detecting server public IP..."
  PUBLIC_IP="$(curl -fs4 --max-time 10 https://api.ipify.org 2>/dev/null \
            || curl -fs4 --max-time 10 https://ifconfig.me 2>/dev/null \
            || curl -fs4 --max-time 10 https://checkip.amazonaws.com 2>/dev/null \
            || curl -fs4 --max-time 10 https://icanhazip.com 2>/dev/null \
            || true)"
else
  ok "Public IP from PARASTOO_PUBLIC_IP: ${PUBLIC_IP}"
fi
[ -n "$PUBLIC_IP" ] || die "Could not detect the public IP. Set it manually: PARASTOO_PUBLIC_IP=1.2.3.4 PARASTOO_BASE_DOMAIN=example.com bash install.sh"
if [ -z "$BASE_DOMAIN" ]; then
  BASE_DOMAIN="${PUBLIC_IP}.nip.io"
  ok "Temporary domain: ${BASE_DOMAIN} (real domain is set later from the web panel)"
  # cross-check: the detected IP should be plausible as the INBOUND IP.
  # If it is not bound to any local interface, egress != inbound is likely
  # and the domain above will not resolve to this server.
  # v0.3.19 — field incident (server rebuild 2026-11-05): the egress echo
  # (ipify) returned the provider NAT IP (205.x) while the real inbound IP
  # sat on the NIC (193.x) — the installer only WARNED and built the whole
  # install around an unreachable nip.io domain. Auto-correct instead:
  # candidate inbound IPs = public IPs actually bound to interfaces
  # (RFC1918/CGNAT/link-local/docker bridges excluded), and when exactly
  # one such IP exists, prefer it over the egress echo.
  INTF_IPS="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 \
              | grep -vE '^(10\.|127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.)' \
              | paste -sd' ' - 2>/dev/null || true)"
  [ -n "$INTF_IPS" ] || INTF_IPS="$(hostname -I 2>/dev/null || true)"
  if [ -n "$INTF_IPS" ] && ! printf '%s\n' $INTF_IPS | grep -qx "$PUBLIC_IP"; then
    warn "Detected IP ${PUBLIC_IP} is NOT bound to any interface of this server (interfaces: ${INTF_IPS})."
    warn "Outbound traffic probably leaves via another IP (NAT / VPN / provider route)."
    if [ "$(printf '%s\n' $INTF_IPS | grep -c . )" = "1" ]; then
      INBOUND_IP="$(printf '%s\n' $INTF_IPS | head -n1)"
      warn "Auto-correcting to the interface (inbound) IP: ${INBOUND_IP}"
      PUBLIC_IP="$INBOUND_IP"
      BASE_DOMAIN="${PUBLIC_IP}.nip.io"
      ok "Inbound IP: ${PUBLIC_IP} — temporary domain: ${BASE_DOMAIN}"
    else
      warn "Multiple interface IPs — cannot auto-pick; re-run with the real inbound IP:"
      say "  curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo PARASTOO_PUBLIC_IP=<real-IP> bash"
      say "  (or set the domain later in the panel wizard: /setup/domain)"
    fi
  fi
else
  ok "Domain from env: ${BASE_DOMAIN}"
fi

BASE_DOMAIN="${BASE_DOMAIN%,}"
PANEL_DOMAIN="panel.${BASE_DOMAIN}"
GIT_DOMAIN="git.${BASE_DOMAIN}"
APPS_DOMAIN="apps.${BASE_DOMAIN}"

# TLS: on for a real domain, off for raw nip.io (certificate limitation)
# default install = nip.io + HTTP; the web wizard enables the real domain + HTTPS
case "$BASE_DOMAIN" in
  *.nip.io) TLS_ENABLED="false"; warn "Domain is nip.io; HTTPS disabled (enable later from the web panel with a real domain)." ;;
  *)        TLS_ENABLED="true" ;;
esac

ACME_EMAIL="${PARASTOO_ACME_EMAIL:-admin@${BASE_DOMAIN}}"
TRAEFIK_ENTRYPOINTS="web"; TRAEFIK_TLS="false"
if [ "$TLS_ENABLED" = "true" ]; then TRAEFIK_ENTRYPOINTS="web,websecure"; TRAEFIK_TLS="true"; fi

GITEA_ROOT_URL_SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && GITEA_ROOT_URL_SCHEME="https"

# ---------- 3) secrets and .env ----------
mark "3) secrets + .env"
say "Generating secure secrets + setup token..."
gen() { openssl rand -hex 24; }
# one-time setup token: printed only here; the web wizard consumes it
SETUP_TOKEN="$(openssl rand -hex 6 | sed -E 's/(.{4})(.{4})(.{4})/\1-\2-\3/')"

# explicit env beats the previous .env for the mirror + domain vars —
# captured BEFORE .env is sourced (sourcing would overwrite them)
_EXPLICIT_PIP="${PARASTOO_PIP_INDEX_URL:-}"
_EXPLICIT_NPM="${PARASTOO_NPM_REGISTRY:-}"
_EXPLICIT_BASE="${PARASTOO_BASE_DOMAIN:-}"
_EXPLICIT_PUBLIC_IP="${PARASTOO_PUBLIC_IP:-}"

if [ -f .env ]; then
  warn ".env already exists; keeping previous secrets."
  # v0.2.15: a legacy .env can hold unquoted values with spaces (real crash:
  # PARASTOO_DEPLOYED_AT=2026-11-04 10:13:46 -> "./.env: line 40: 10:13:46:
  # command not found" -> set -e killed the whole installer mid-update).
  # Source tolerantly: keep what parses, never abort. The heredoc below
  # rewrites the file with quoted values, so this only matters once.
  set +e
  set -a; . ./.env 2>/dev/null; set +a
  set -e
  if [ -n "$_EXPLICIT_PIP" ]; then PARASTOO_PIP_INDEX_URL="$_EXPLICIT_PIP"; fi
  if [ -n "$_EXPLICIT_NPM" ]; then PARASTOO_NPM_REGISTRY="$_EXPLICIT_NPM"; fi
fi

# v0.2.19 fix (field incident 2026-11-04): sourcing the OLD .env above would
# clobber the freshly derived domain identity — a stale PANEL_DOMAIN (written
# by an install whose egress-IP detection picked a NAT outbound IP) then
# silently won over the PARASTOO_BASE_DOMAIN the user passed NOW, got
# rewritten into the new .env, into the Traefik label router and into the
# final printed panel URL. Explicit env > old .env, and everything downstream
# of BASE_DOMAIN is re-derived here so no stale value can survive.
if [ -n "$_EXPLICIT_BASE" ]; then
  if [ -n "${BASE_DOMAIN:-}" ] && [ "$BASE_DOMAIN" != "$_EXPLICIT_BASE" ]; then
    warn "Domain re-targeted by this run: ${BASE_DOMAIN} -> ${_EXPLICIT_BASE} (old .env value does not win over an explicit PARASTOO_BASE_DOMAIN)."
  fi
  BASE_DOMAIN="$_EXPLICIT_BASE"
elif [ -z "${BASE_DOMAIN:-}" ]; then
  BASE_DOMAIN="${PUBLIC_IP:-}.nip.io"
fi
if [ -n "$_EXPLICIT_PUBLIC_IP" ]; then PARASTOO_PUBLIC_IP="$_EXPLICIT_PUBLIC_IP"; fi
PANEL_DOMAIN="panel.${BASE_DOMAIN}"
GIT_DOMAIN="git.${BASE_DOMAIN}"
APPS_DOMAIN="apps.${BASE_DOMAIN}"
case "$BASE_DOMAIN" in
  *.nip.io) TLS_ENABLED="false" ;;
  *)        TLS_ENABLED="true" ;;
esac
ACME_EMAIL="${PARASTOO_ACME_EMAIL:-admin@${BASE_DOMAIN}}"
TRAEFIK_ENTRYPOINTS="web"; TRAEFIK_TLS="false"
if [ "$TLS_ENABLED" = "true" ]; then TRAEFIK_ENTRYPOINTS="web,websecure"; TRAEFIK_TLS="true"; fi
GITEA_ROOT_URL_SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && GITEA_ROOT_URL_SCHEME="https"

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(gen)}"
GITEA_DB_PASSWORD="${GITEA_DB_PASSWORD:-$(gen)}"
GITEA_SECRET_KEY="${GITEA_SECRET_KEY:-$(gen)}"
PARASTOO_SECRET="${PARASTOO_SECRET:-$(gen)}"
GITEA_ADMIN_USER="${GITEA_ADMIN_USER:-parastoo-system}"
GITEA_ADMIN_EMAIL="${GITEA_ADMIN_EMAIL:-system@${BASE_DOMAIN}}"
GITEA_ADMIN_PASSWORD="${GITEA_ADMIN_PASSWORD:-$(gen)}"
PARASTOO_TZ="${PARASTOO_TZ:-Asia/Tehran}"
PARASTOO_DEBT_LIMIT="${PARASTOO_DEBT_LIMIT:-0}"
PARASTOO_COMMISSION="${PARASTOO_COMMISSION:-10}"
PARASTOO_SETUP_TOKEN="${PARASTOO_SETUP_TOKEN:-$SETUP_TOKEN}"
PARASTOO_PUBLIC_IP="${PARASTOO_PUBLIC_IP:-$PUBLIC_IP}"

# --- آپدیت خودکار از گیت‌هاب (v0.2.12): نصب اولیه دستی، آپدیت‌های بعدی خودکار ---
PARASTOO_AUTO_UPDATE="${PARASTOO_AUTO_UPDATE:-true}"
PARASTOO_AUTO_UPDATE_INTERVAL="${PARASTOO_AUTO_UPDATE_INTERVAL:-120}"
PARASTOO_UPDATE_CHANNEL="${PARASTOO_UPDATE_CHANNEL:-main}"
PARASTOO_GH_PROXY="${PARASTOO_GH_PROXY:-}"
# --- حالت توسعه‌دهنده: صفحه /dev و API گزارش/لاگ (فقط برای نصب آزمایشی true) ---
PARASTOO_DEV_MODE="${PARASTOO_DEV_MODE:-false}"
PARASTOO_DEV_TOKEN="${PARASTOO_DEV_TOKEN:-}"
# --- آخرین استقرار (بعداً auto-update.sh به‌روزرسانی‌اش می‌کند) ---
# v0.2.13 fix: مقدار پیش‌فرضِ همیشه‌حاضر — bootstrap از تاربال (بدون .git) نصب
# می‌کند و با set -u نبودِ این متغیر، heredoc نوشتن .env را می‌کشت.
# منبع مقدار: env-pass از bootstrap.sh (کامیت دقیق رفرنس) <- گیت محلی <- خالی
_NOW_TS="$(date '+%F %T')"
PARASTOO_DEPLOYED_AT="${PARASTOO_DEPLOYED_AT:-$_NOW_TS}"
PARASTOO_DEPLOYED_COMMIT="${PARASTOO_DEPLOYED_COMMIT:-}"
if [ -z "$PARASTOO_DEPLOYED_COMMIT" ] && command -v git >/dev/null 2>&1 && [ -d .git ]; then
  PARASTOO_DEPLOYED_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
fi

# sanctions: pip/npm mirrors — priority: explicit env <- existing .env (rechecked;
# replaced by the fresh detection if it stopped answering) <- auto-detect <- empty
if [ -z "$_EXPLICIT_PIP" ] && [ -n "$DETECTED_PIP_MIRROR" ] && [ -n "${PARASTOO_PIP_INDEX_URL:-}" ] \
   && [ "$PARASTOO_PIP_INDEX_URL" != "$DETECTED_PIP_MIRROR" ] \
   && [ "$(timeout 12 curl -fsSL -o /dev/null -w '%{http_code}' --max-time 8 "${PARASTOO_PIP_INDEX_URL%/}/" 2>/dev/null)" != "200" ]; then
  warn "pip mirror from the old .env is not answering; switching to the live one found now."
  PARASTOO_PIP_INDEX_URL="$DETECTED_PIP_MIRROR"
fi
if [ -z "$_EXPLICIT_NPM" ] && [ -n "$DETECTED_NPM_MIRROR" ] && [ -n "${PARASTOO_NPM_REGISTRY:-}" ] \
   && [ "$PARASTOO_NPM_REGISTRY" != "$DETECTED_NPM_MIRROR" ] \
   && [ "$(timeout 12 curl -fsSL -o /dev/null -w '%{http_code}' --max-time 8 "$PARASTOO_NPM_REGISTRY/-/ping" 2>/dev/null)" != "200" ]; then
  warn "npm registry from the old .env is not answering; switching to the live one found now."
  PARASTOO_NPM_REGISTRY="$DETECTED_NPM_MIRROR"
fi
PARASTOO_PIP_INDEX_URL="${PARASTOO_PIP_INDEX_URL:-${DETECTED_PIP_MIRROR:-}}"
PARASTOO_NPM_REGISTRY="${PARASTOO_NPM_REGISTRY:-${DETECTED_NPM_MIRROR:-}}"
[ -n "$PARASTOO_PIP_INDEX_URL" ] && ok "pip mirror active: $PARASTOO_PIP_INDEX_URL"
[ -n "$PARASTOO_NPM_REGISTRY" ] && ok "npm mirror active: $PARASTOO_NPM_REGISTRY"

# --- v0.3.18: درس میدانی EADDRINUSE (LESSONS) — systemd-resolved روی
# 127.0.0.53:53 گوش می‌دهد و بایند TCP 0.0.0.0:53 داکر در سطح کرنل با آن
# تداخل می‌کند → کانتینر dns بالا نمی‌آید. اگر resolved فعال بود، بایند DNS
# را خودکار به IP عمومی محدود می‌کنیم (بدون دست‌زدن به resolved).
# v0.3.19 — درس میدانی بازسازی سرور: ss در اوبونتو ۲۴.۰۴ این آدرس را با پسوند
# اسکوپ چاپ می‌کند (127.0.0.53%lo:53) و الگوی ساده هرگز نمی‌دید → بایند خالی ماند
# و همانی EADDRINUSE رخ داد. الگو حالا پسوند %scope را هم می‌پذیرد (۱۲۷.0.0.54 هم).
if [ -z "${PARASTOO_DNS_BIND_IP:-}" ] && [ -n "${PUBLIC_IP:-}" ] \
   && (ss -tln 2>/dev/null | grep -Eq '127\.0\.0\.5[34](%[a-zA-Z0-9._-]+)?:53'); then
  PARASTOO_DNS_BIND_IP="${PUBLIC_IP}"
  ok "systemd-resolved detected on 127.0.0.53:53 -> DNS binds to ${PUBLIC_IP} (PARASTOO_DNS_BIND_IP)"
fi
PARASTOO_DNS_BIND_IP="${PARASTOO_DNS_BIND_IP:-}"

cat > .env <<ENV
# ===== Parastoo (generated by install.sh) =====
BASE_DOMAIN=${BASE_DOMAIN}
PANEL_DOMAIN=${PANEL_DOMAIN}
GIT_DOMAIN=${GIT_DOMAIN}
APPS_DOMAIN=${APPS_DOMAIN}
TLS_ENABLED=${TLS_ENABLED}
ACME_EMAIL=${ACME_EMAIL}
TRAEFIK_ENTRYPOINTS=${TRAEFIK_ENTRYPOINTS}
TRAEFIK_TLS=${TRAEFIK_TLS}
GITEA_ROOT_URL=${GITEA_ROOT_URL_SCHEME}://${GIT_DOMAIN}/

POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
GITEA_DB_PASSWORD=${GITEA_DB_PASSWORD}
GITEA_SECRET_KEY=${GITEA_SECRET_KEY}
PARASTOO_SECRET=${PARASTOO_SECRET}
GITEA_ADMIN_USER=${GITEA_ADMIN_USER}
GITEA_ADMIN_EMAIL=${GITEA_ADMIN_EMAIL}
GITEA_ADMIN_PASSWORD=${GITEA_ADMIN_PASSWORD}

PARASTOO_TZ=${PARASTOO_TZ}
PARASTOO_DEBT_LIMIT=${PARASTOO_DEBT_LIMIT}
PARASTOO_COMMISSION=${PARASTOO_COMMISSION}
PARASTOO_SETUP_TOKEN=${PARASTOO_SETUP_TOKEN}
PARASTOO_PUBLIC_IP=${PARASTOO_PUBLIC_IP}
# --- v0.3.18: بایند کانتینر dns (خالی = 0.0.0.0 پیش‌فرض compose) ---
PARASTOO_DNS_BIND_IP=${PARASTOO_DNS_BIND_IP}

# --- آپدیت خودکار از گیت‌هاب (v0.2.12) ---
# هر push به شاخهٔ کانال = استقرار خودکار روی همین سرور (تایمر systemd)
PARASTOO_AUTO_UPDATE=${PARASTOO_AUTO_UPDATE}
PARASTOO_AUTO_UPDATE_INTERVAL=${PARASTOO_AUTO_UPDATE_INTERVAL}
PARASTOO_UPDATE_CHANNEL=${PARASTOO_UPDATE_CHANNEL}
PARASTOO_GH_PROXY=${PARASTOO_GH_PROXY}

# --- حالت توسعه‌دهنده: صفحه /dev + /api/dev/report|logs|updates (گزارش خطاها) ---
# روی نصب عملیاتی false بماند. اگر PARASTOO_DEV_TOKEN ست شود، گزارش با هدر X-Dev-Token هم در دسترس است.
PARASTOO_DEV_MODE=${PARASTOO_DEV_MODE}
PARASTOO_DEV_TOKEN=${PARASTOO_DEV_TOKEN}

# --- آخرین استقرار (توسط auto-update.sh نگه‌داری می‌شود؛ /api/version نشان می‌دهد) ---
# v0.2.15: مقدارِ با فاصله (زمان) باید کوتیشن داشته باشد وگرنه source کردن .env
# در اجرای بعدی نصب‌ر را می‌کشد («10:13:46: command not found»)
PARASTOO_DEPLOYED_COMMIT="${PARASTOO_DEPLOYED_COMMIT}"
PARASTOO_DEPLOYED_AT="${PARASTOO_DEPLOYED_AT}"

# --- sanctions (optional; docs/SANCTIONS.md) ---
# pip mirror for Python builds, e.g.: https://package-mirror.liara.ir/repository/pypi/simple
PARASTOO_PIP_INDEX_URL=${PARASTOO_PIP_INDEX_URL}
# npm registry for node builds, e.g.: https://registry.npmjs.ir
PARASTOO_NPM_REGISTRY=${PARASTOO_NPM_REGISTRY}
ENV
chmod 600 .env
ok "Configuration saved to .env."

# ---------- 4) Traefik configuration ----------
mark "4) traefik config"
say "Building Traefik configuration..."
mkdir -p config/dynamic backups logs

# وضعیت اولیهٔ استقرار — منبع /api/version و مبنای مقایسهٔ auto-update
_CODE_VER="$(sed -n 's/^ *VERSION: *str *= *"\([^"]*\)".*/\1/p' core/app/config.py 2>/dev/null | head -1)"
{
  echo "version=${_CODE_VER:-unknown}"
  echo "commit=${PARASTOO_DEPLOYED_COMMIT:-}"
  echo "time=$(date '+%F %T')"
} > .parastoo-deploy

REDIRECT_BLOCK=""
if [ "$TLS_ENABLED" = "true" ]; then
  REDIRECT_BLOCK="    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https"
fi

awk -v block="$REDIRECT_BLOCK" -v email="$ACME_EMAIL" '
  { gsub(/__WEB_REDIRECT__/, block); gsub(/__ACME_EMAIL__/, email) }
  { print }
' deploy/templates/traefik.yml.tmpl > config/traefik.yml

[ "$TLS_ENABLED" = "true" ] && ok "HTTP -> HTTPS redirect enabled."
chmod +x postgres-init/*.sh

# ---------- 4a) db credential reconcile (v0.2.15) ----------
# self-heal: the postgres volume keeps the FIRST install's passwords (init
# scripts run only on an empty volume). If .env ever drifts from the volume
# (installer crash mid-.env + regenerated secrets), gitea/core die with
# "password authentication failed" and the stack never starts. .env is the
# source of truth -> align the database to it before any `compose up`.
mark "4a) db credential reconcile"
if ! bash scripts/db-reconcile.sh; then
  warn "db reconcile did not complete (continuing; compose up reports the real error if any)."
fi

# ---------- 4b) CLI + auto-update timer — BEFORE the stack (v0.2.14) ----------
# چرا اینجا؟ تا اگر بالا آوردن استک به هر دلیلی شکست خورد، تایمر آپدیت خودکار
# همچنان مسلح باشد: هر push بعدی به گیت‌هاب خودش استک را تعمیر/استقرار می‌کند
# (auto-update.sh خودش docker compose up -d --build را اجرا می‌کند). بدون این،
# هر باگ استقرار به یک چرخهٔ «دستی اجرا کن، دستی بفرست» تبدیل می‌شد.
mark "4b) CLI + auto-update timer"
cp -f bin/parastoo /usr/local/bin/parastoo
chmod +x /usr/local/bin/parastoo
echo "$INSTALL_DIR" > /etc/parastoo-install-path
ok "parastoo CLI installed (status/logs/update/autoupdate/backup)."
if [ "${PARASTOO_AUTO_UPDATE:-true}" = "true" ] && command -v systemctl >/dev/null 2>&1; then
  say "Installing the auto-update timer (checks GitHub every ${PARASTOO_AUTO_UPDATE_INTERVAL}s)..."
  mkdir -p /etc/systemd/system
  for _u in parastoo-autoupdate.service parastoo-autoupdate.timer; do
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "deploy/systemd/$_u" > "/etc/systemd/system/$_u" 2>/dev/null || true
  done
  systemctl daemon-reload 2>/dev/null || true
  if systemctl enable --now parastoo-autoupdate.timer 2>/dev/null; then
    ok "Auto-update enabled: every push to '${PARASTOO_UPDATE_CHANNEL:-main}' on GitHub deploys automatically."
  else
    warn "Could not enable the timer; check: systemctl status parastoo-autoupdate.timer"
  fi
elif [ "${PARASTOO_AUTO_UPDATE:-true}" = "true" ]; then
  warn "systemd not available; add to cron instead:"
  warn "  * * * * * root bash $INSTALL_DIR/scripts/auto-update.sh"
else
  warn "Auto-update disabled (PARASTOO_AUTO_UPDATE=false in .env) — update manually: sudo parastoo update"
fi

# ---------- 5) bring up the stack ----------
mark "5) docker compose up"
# recreates interrupted by a daemon restart / failed `up` leave temp-named
# orphans ("<12hex>_parastoo-<svc>") that block the next `up` with a name
# Conflict (real case: container name "/4b14fefb6982_parastoo-core" is
# already in use) — remove them first
STALE="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^[0-9a-f]{12}_parastoo-' || true)"
if [ -n "$STALE" ]; then
  warn "Removing stale containers from an interrupted recreate: $(echo "$STALE" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  docker rm -f $STALE >/dev/null 2>&1 || true
fi
say "Building and starting the full stack (first run may take a few minutes)..."
if ! docker compose up -d --build; then
  echo ""
  warn "The auto-update timer is already armed — pushing the fix to GitHub deploys it automatically."
  say "Diagnose the failing container:"
  say "  cd $INSTALL_DIR && sudo docker compose ps"
  say "  sudo docker logs parastoo-<name> --tail 60   # e.g. parastoo-gitea"
  say "  sudo bash $INSTALL_DIR/scripts/doctor.sh"
  die "Stack failed to start. If the error is about image pulls:
  1) build the offline bundle: bash scripts/fetch-images.sh (on an unsanctioned machine) -> put the offline-images/ folder here -> run install.sh again
  2) or set a healthy mirror: PARASTOO_REGISTRY_MIRRORS=\"https://docker.arvancloud.ir\" sudo -E bash install.sh
  3) if the error mentions pypi.org / files.pythonhosted.org: set a pip mirror and re-run:
     PARASTOO_PIP_INDEX_URL=\"https://package-mirror.liara.ir/repository/pypi/simple\" sudo -E bash install.sh
  Full guide: docs/SANCTIONS.md"
fi

# ---------- 6) wait for core health ----------
mark "6) health wait"
say "Waiting for the stack to become healthy..."
ATTEMPTS=60
for i in $(seq 1 $ATTEMPTS); do
  if docker compose exec -T core curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
    ok "Parastoo core is up."
    break
  fi
  [ "$i" = "$ATTEMPTS" ] && { docker compose logs --tail 30 core; die "Parastoo core did not become ready. See the logs above."; }
  sleep 3
done

# ---------- 6b) panel through Traefik — the real user path ----------
# Field failure: core can be healthy on :8000 while Traefik serves zero routes
# (panel 404 from everywhere) — the installer must test the FULL user path.
mark "6b) panel via traefik"
PANEL_CODE=""
for i in $(seq 1 10); do
  PANEL_CODE="$(timeout 8 curl -sS -o /dev/null -w '%{http_code}' -H "Host: $PANEL_DOMAIN" http://127.0.0.1/healthz 2>/dev/null || true)"
  [ "$PANEL_CODE" = "200" ] && break
  sleep 2
done
if [ "$PANEL_CODE" = "200" ]; then
  ok "Panel answers through Traefik for Host $PANEL_DOMAIN."
else
  warn "Panel is NOT reachable through Traefik (Host $PANEL_DOMAIN -> HTTP ${PANEL_CODE:-none})."
  warn "Core itself is healthy, so the fault is in the Traefik layer (docker provider / labels / entrypoint)."
  say "Diagnose now:  sudo docker logs parastoo-traefik --tail 40   and:  bash scripts/doctor.sh"
  # Name the exact root cause when it is the Docker 29 / old-Traefik incompatibility
  if docker logs parastoo-traefik --tail 100 2>&1 | grep -q 'is too old'; then
    warn "Root cause found: the host Docker Engine is newer than this Traefik image supports (Docker 29 needs Traefik >= 3.6.6)."
    say "Fix:  cd $INSTALL_DIR"
    say "      sed -i 's|image: traefik:.*|image: traefik:v3.6.6|' docker-compose.yml && sudo docker compose up -d traefik"
  fi
fi

SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && SCHEME="https"
mark "done"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Parastoo is installed!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo "  Three steps to finish (all in your browser):"
echo "    1) Open the panel URL:"
echo ""
echo -e "       ${BLUE}${SCHEME}://${PANEL_DOMAIN}${NC}"
echo ""
echo "  If this URL does NOT open: the server's inbound IP differs from the"
echo "  detected outbound IP (NAT/VPN/provider route). Re-run with the real IP:"
echo -e "       ${BLUE}curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo PARASTOO_PUBLIC_IP=<real-IP> bash${NC}"
echo "  (or fix the domain later in the panel wizard: /setup/domain)"
echo ""
echo -e "    2) Enter the one-time setup token (copy it now):"
echo ""
echo -e "       ${BLUE}${PARASTOO_SETUP_TOKEN}${NC}"
echo ""
echo "    3) Create the admin account and set your domain/HTTPS in the wizard."
echo ""
echo "  System credentials (also stored in .env):"
echo "         Gitea admin password: ${GITEA_ADMIN_PASSWORD}"
echo ""
echo "  Management commands:  parastoo status | logs | update | autoupdate | backup | restart"
echo "  Auto-update:  every push to GitHub '${PARASTOO_UPDATE_CHANNEL}' auto-deploys (parastoo autoupdate status)"
echo "  Version API:  ${SCHEME}://${PANEL_DOMAIN}/api/version"
echo "  Sanctions & offline bundle:  docs/SANCTIONS.md"
echo ""
