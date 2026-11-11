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
#  Details: docs/SANCTIONS.fa.md
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

# ---------- Docker daemon health gate (v0.3.24) ----------
# Real-world failure (fresh Ubuntu 24.04, 2026-11-06): Ubuntu's docker.io 29.x
# ships docker.service with Requires=docker.socket but NO After= ordering — on
# the very first start dockerd can race the socket unit, die with
# "failed to load listeners: no sockets found via socket activation", and trip
# systemd's start-rate-limit. Every later `systemctl start docker` then fails
# instantly and was silently swallowed by `|| true`, so the installer kept
# going and died minutes later with the misleading "core does not answer
# /healthz". Fix: actually verify `docker info`; if the daemon is wedged, run
# the exact recovery sequence (reset-failed -> socket -> service -> wait)
# before continuing; if it still cannot come up, fail HERE with the journal.
wait_docker_info() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    docker info >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}
ensure_docker_daemon() {
  if wait_docker_info; then return 0; fi
  warn "Docker daemon is not answering — trying the socket-activation recovery..."
  systemctl reset-failed docker.service docker.socket 2>/dev/null || true
  systemctl start docker.socket 2>/dev/null || true
  sleep 1
  systemctl start docker.service 2>/dev/null || true
  if wait_docker_info; then
    ok "Docker daemon recovered."
    return 0
  fi
  echo "        --- last journal lines (docker.service / docker.socket): ---" >&2
  journalctl -u docker.service -u docker.socket --no-pager -n 12 2>/dev/null | sed 's/^/        /' >&2
  die "Docker daemon did not come up — a host problem, not a network problem. See the journal above; docs/SANCTIONS.fa.md, section 'Docker install'."
}

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
      || die "Docker install failed. Manual fix: docs/SANCTIONS.fa.md, section 'Docker install'"
    ok "Docker installed from Ubuntu repositories."
  fi
else
  ok "Docker present: $(docker --version)"
fi
systemctl enable --now docker >/dev/null 2>&1 || true
ensure_docker_daemon

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin not found. Install docker-compose-v2 (docs/SANCTIONS.fa.md)."
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
    # a restart that silently failed used to surface much later as
    # "core does not answer /healthz"; verify the daemon really came back
    ensure_docker_daemon
    return 0
  fi
  return 1
}

mark "1c) registry mirrors"
if [ -n "${PARASTOO_REGISTRY_MIRRORS:-}" ]; then
  say "Configuring mirrors from env (PARASTOO_REGISTRY_MIRRORS)..."
  apply_mirrors "$PARASTOO_REGISTRY_MIRRORS" \
    && ok "Mirrors configured." \
    || die "Failed to set mirrors; edit /etc/docker/daemon.json manually (docs/SANCTIONS.fa.md)."
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
      warn "No live mirror found — continuing with the offline bundle; for future builds see docs/SANCTIONS.fa.md."
    fi
  else
    ALIVE="$(bash scripts/check-mirrors.sh || true)"
    if [ -n "$ALIVE" ]; then
      apply_mirrors "$ALIVE" \
        && ok "$(echo "$ALIVE" | wc -l) live mirror(s) active in /etc/docker/daemon.json (fastest first)." \
        || warn "Failed to write mirrors — docs/SANCTIONS.fa.md"
    else
      warn "No live mirror found; update the list: deploy/mirrors.txt or use PARASTOO_REGISTRY_MIRRORS"
      warn "Without mirrors, image pulls fail — reliable route: offline bundle (docs/SANCTIONS.fa.md, layer 3)"
    fi
    # final verification: a small real pull (same image the backup command needs)
    if timeout 90 docker pull -q alpine:3.20 >/dev/null 2>&1; then
      ok "Final check: pull through the mirror works."
    elif ! docker info >/dev/null 2>&1; then
      # a pull can never test the mirrors when the daemon itself is down — say so
      warn "Test pull failed because the Docker daemon is down (not a mirror problem) — see journalctl -u docker, then re-run install.sh."
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

# v0.3.27 — a pip mirror only counts as live if a REAL package file downloads.
# Field bug (2026-11-06, fresh install): runflare's index answered 200 but every
# /packages/... fetch returned 402 Payment Required; the old index-only probe
# passed it and broke out of the loop, so the core image build died on
# `pip install fastapi` (HTTP 402). Meanwhile liara — perfectly healthy — was
# never reached. Probe = fetch the six wheel from the mirror itself.
# Relative hrefs (`../../packages/...`) resolve to different depths per mirror
# layout (liara nests under /repository/pypi/), so the absolute URL is built
# with urllib.parse.urljoin — python3 is guaranteed on Ubuntu 24.04.
pypi_file_probe() {  # $1 = mirror index URL; rc 0 = a real wheel actually downloaded
  local page_url="${1%/}/six/"
  local link resolved
  link="$(timeout 14 curl -fsSL --max-time 10 "$page_url" 2>/dev/null \
      | grep -oE 'href="[^"]+six-[0-9][^"]*\.whl[^"]*"' | head -1 | sed 's/^href="//;s/"$//')" || true
  [ -n "$link" ] || return 1
  resolved="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.urljoin(sys.argv[1], sys.argv[2].split("#")[0]))' "$page_url" "$link" 2>/dev/null)" || return 1
  [ -n "$resolved" ] || return 1
  timeout 25 curl -fsSL --max-time 18 -o /dev/null "$resolved" 2>/dev/null
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
    say "Probing Python package mirrors (a real package file must download)..."
    for m in \
      https://package-mirror.liara.ir/repository/pypi/simple \
      https://mirror.abrha.net/repository/pypi/simple \
      https://mirror-pypi.runflare.com/simple \
      https://mirror.kargadan.ir/repository/pypi-group/simple \
      https://mirrors.arvancloud.ir/pypi/simple \
      https://pypi.tuna.tsinghua.edu.cn/simple \
      https://mirrors.aliyun.com/pypi/simple \
      https://pypi.mirrors.ustc.edu.cn/simple \
      https://mirrors.cloud.tencent.com/pypi/simple; do
      # the index page AND a real wheel download must both succeed — a 200 on
      # the index alone is exactly how runflare (402 on files) slipped through
      printf '      testing %-58s' "${m%/}/"
      if pypi_file_probe "$m"; then
        echo "ok"
        DETECTED_PIP_MIRROR="$m"; ok "Live pip mirror: $m (will be written to .env)"; break
      fi
      echo "fail"
    done
    [ -n "$DETECTED_PIP_MIRROR" ] || warn "No live pip mirror found; the core build may fail — docs/SANCTIONS.fa.md, layer 4"
  fi
fi
if [ -z "${PARASTOO_NPM_REGISTRY:-}" ]; then
  probe_full "registry.npmjs.org" "https://registry.npmjs.org/-/ping" && NPM_OK=yes
  if [ "$NPM_OK" = yes ]; then
    ok "Official npm is reachable. No mirror needed."
  else
    say "Probing npm mirrors (ping AND a real tarball download must pass)..."
    for m in https://registry.npmjs.ir https://registry.npmmirror.com; do
      printf '      testing %-58s' "$m/-/ping"
      # same false-positive class as PyPI: /-/ping can 200 while package
      # tarball routes are dead — left-pad is ~2 KB, standard registry layout
      if [ "$(timeout 12 curl -fsSL -o /dev/null -w '%{http_code}' --max-time 8 "$m/-/ping" 2>/dev/null)" = "200" ] \
         && timeout 25 curl -fsSL -o /dev/null --max-time 18 "$m/left-pad/-/left-pad-1.3.0.tgz" 2>/dev/null; then
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

# cross-check: the detected IP should be plausible as the INBOUND IP.
# If it is not bound to any local interface, egress != inbound is likely.
# v0.3.19 — field incident (server rebuild 2026-11-05): the egress echo
# (ipify) returned the provider NAT IP (205.x) while the real inbound IP
# sat on the NIC (193.x) — the installer only WARNED and built the whole
# install around an unreachable nip.io domain. Auto-correct instead:
# candidate inbound IPs = public IPs actually bound to interfaces
# (RFC1918/CGNAT/link-local/docker bridges excluded), and when exactly
# one such IP exists, prefer it over the egress echo.
# v0.3.25 — the correction now runs BEFORE the BASE_DOMAIN branch: with an
# explicit PARASTOO_BASE_DOMAIN the egress echo used to leak into
# PARASTOO_DNS_BIND_IP and the base-zone A records — PowerDNS tried to bind
# a non-local address ("cannot assign requested address"), the container
# reported healthy with ZERO host ports, and authoritative DNS was silently
# dead (field: fresh install with BASE_DOMAIN=zeolitecenter.ir, 2026-11-06).
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
    ok "Inbound IP: ${PUBLIC_IP}"
  else
    warn "Multiple interface IPs — cannot auto-pick; re-run with the real inbound IP:"
    say "  curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo env PARASTOO_PUBLIC_IP=<real-IP> bash"
    say "  (or set the domain later in the panel wizard: /setup/domain)"
  fi
fi

if [ -z "$BASE_DOMAIN" ]; then
  # v0.5.14 — persisted domain identity (field 2026-11-09): the owner-ordered
  # FULL uninstall wipes /opt/parastoo (.env included) while the registry
  # delegation keeps pointing at this server — the next install then fell
  # back to nip.io and the real domain died silently. /etc/parastoo-domain
  # (outside the install tree AND /var/lib/parastoo) is the memory: install
  # re-adopts it, section 6d re-provisions the base zone and re-applies
  # HTTPS — a reinstall ends with the SAME live domain, zero questions, zero
  # outside help. nip.io is IP-derived and never persisted/adopted. An
  # existing .env (sourced below) still wins over the marker — live truth
  # first.
  _MARK_DOM="$(head -n1 /etc/parastoo-domain 2>/dev/null | tr -d ' "' || true)"
  case "$_MARK_DOM" in
    *.nip.io|"")
      BASE_DOMAIN="${PUBLIC_IP}.nip.io"
      ok "Temporary domain: ${BASE_DOMAIN} (real domain is set later from the web panel)" ;;
    *)
      BASE_DOMAIN="$_MARK_DOM"
      ok "Domain identity restored from /etc/parastoo-domain: ${BASE_DOMAIN}" ;;
  esac
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

# v0.5.14 — persist the real domain OUTSIDE the install tree: /opt/parastoo
# (with .env) is disposable by design (owner-ordered full cleanup removes it),
# but the registry delegation keeps pointing at this server, so the next
# install must adopt the same domain automatically (the marker read in step 2
# pairs with this write). nip.io is temporary and never persisted. Best-effort:
# a read-only /etc must never abort the installer.
case "$BASE_DOMAIN" in
  *.nip.io) : ;;
  *) printf '%s\n' "$BASE_DOMAIN" > /etc/parastoo-domain 2>/dev/null \
       && chmod 644 /etc/parastoo-domain 2>/dev/null || true ;;
esac

ACME_EMAIL="${PARASTOO_ACME_EMAIL:-admin@${BASE_DOMAIN}}"
TRAEFIK_ENTRYPOINTS="web"; TRAEFIK_TLS="false"
if [ "$TLS_ENABLED" = "true" ]; then TRAEFIK_ENTRYPOINTS="web,websecure"; TRAEFIK_TLS="true"; fi
GITEA_ROOT_URL_SCHEME="http"; [ "$TLS_ENABLED" = "true" ] && GITEA_ROOT_URL_SCHEME="https"

# --- CA گواهی (v0.3.30): letsencrypt (پیش‌فرض) | zerossl — برای نصب‌هایی که
# سقف نرخ لت‌انکریپت (۵ گواهی در ۷ روز برای هر مجموعه هاست) آنها را می‌بندد
# (میدان 2026-11-06: پنجمین نصب تازهٔ همان دامنه). پروتکل ACME زروسsl از ایران
# سبز و سریع است؛ BuyPass/Actalis مسدودند؛ ولی زروسsl برای دامنهٔ .ir گواهی
# صادر نمی‌کند (v0.3.31 — فهرست TLDهای محدود). EAB یک‌بار گرفته می‌شود و در
# .env می‌ماند (طبق مستندات ZeroSSL قابل استفادهٔ مجدد است).
ACME_CA="${PARASTOO_ACME_CA:-letsencrypt}"
# v0.3.32 — heredoc-safety (install_env_test): EAB is filled conditionally in
# the zerossl branch below; initialize unconditionally so the .env heredoc
# can never hit an unbound variable (set -u kills install.sh before compose up)
PARASTOO_ACME_EAB_KID="${PARASTOO_ACME_EAB_KID:-}"
PARASTOO_ACME_EAB_HMAC="${PARASTOO_ACME_EAB_HMAC:-}"
# v0.3.31 — درس میدانی دور ششم (نصب زنده): حساب ACME و EAB زروسsl سبز است
# (newAccount 201) ولی هر newOrder برای شناسهٔ .ir با rejectedIdentifier رد
# می‌شود («DNS identifier is disallowed») — فهرست TLDهای محدود زروسsl
# (تحریم صادراتی آمریکا/اروپا؛ help.zerossl.com → Restricted Countries) .ir
# را صراحتاً ممنوع کرده؛ در مقابل LE رسماً .ir صادر می‌کند. شکست صادقانه
# باید همین‌جا باشد نه لحظهٔ صدور گواهی.
if [ "$TLS_ENABLED" = "true" ] && [ "$ACME_CA" = "zerossl" ] \
   && printf '%s' "$BASE_DOMAIN" | grep -qiE '(^|\.)ir$'; then
  die "ZeroSSL does not issue certificates for .ir domains (restricted TLD — US/EU export laws; see docs/SANCTIONS.fa.md). Re-run without PARASTOO_ACME_CA (default letsencrypt — LE does issue .ir certificates)."
fi
ACME_CA_URL="https://acme-v02.api.letsencrypt.org/directory"
ACME_EAB_BLOCK=""
if [ "$TLS_ENABLED" = "true" ] && [ "$ACME_CA" = "zerossl" ]; then
  ACME_CA_URL="https://acme.zerossl.com/v2/DV90"
  if [ -z "${PARASTOO_ACME_EAB_KID:-}" ] && [ -n "${PARASTOO_ZEROSSL_API_KEY:-}" ]; then
    say "ZeroSSL: fetching EAB credentials via API ..."
    _EAB_JSON="$(curl -s --max-time 20 -X POST \
      "https://api.zerossl.com/acme/eab-credentials?access_key=${PARASTOO_ZEROSSL_API_KEY}" || true)"
    PARASTOO_ACME_EAB_KID="$(printf '%s' "$_EAB_JSON" | sed -n 's/.*"eab_kid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
    PARASTOO_ACME_EAB_HMAC="$(printf '%s' "$_EAB_JSON" | sed -n 's/.*"eab_hmac_key"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  fi
  if [ -n "${PARASTOO_ACME_EAB_KID:-}" ] && [ -n "${PARASTOO_ACME_EAB_HMAC:-}" ]; then
    ACME_EAB_BLOCK="      eab:
        kid: \"${PARASTOO_ACME_EAB_KID}\"
        hmacEncoded: \"${PARASTOO_ACME_EAB_HMAC}\""
    say "ACME CA: ZeroSSL (EAB ready, reusable)."
  else
    die "PARASTOO_ACME_CA=zerossl needs EAB credentials: create a free account at zerossl.com, then pass PARASTOO_ZEROSSL_API_KEY (EAB is fetched automatically) or PARASTOO_ACME_EAB_KID + PARASTOO_ACME_EAB_HMAC from the Developer section of the dashboard."
  fi
fi
# بلوک YAML برای قالب ترافیک (کلید + تورفتگی ۶ فاصله) — جدا از URL پروب
ACME_CASERVER_BLOCK="      caServer: \"${ACME_CA_URL}\""
# probe the CA directory — a blocked CA (field: BuyPass times out from Iran)
# is seen honestly here, not hours later in traefik logs
if [ "$TLS_ENABLED" = "true" ]; then
  _ACME_CODE="$(timeout 15 curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$ACME_CA_URL" 2>/dev/null || echo 000)"
  [ "$_ACME_CODE" = "200" ] || warn "ACME CA directory ($ACME_CA) answered '$_ACME_CODE' — certificate issuance may fail (network/sanctions). See docs/SANCTIONS.fa.md."
fi

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

# --- آپدیت از گیت‌هاب (v0.5.13 — فرمان مالک 2026-11-09 «بروزرسانی باید اتوماتیک
# باشد و نباید پرسیده شود»): پیش‌فرض روشن؛ تایمر در §6f پس از سلامتِ استک مسلح
# می‌شود. خاموش‌کردن: sudo parastoo autoupdate off. نصب/حذف/نصب مجدد همچنان فقط صریح.
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
# v0.3.25 — the bind IP must be an address of THIS host (a NAT egress IP is
# not bindable: "cannot assign requested address" -> the container comes up
# "healthy" with zero host ports). Defense in depth even when the NAT
# auto-correct above could not run (e.g. an explicit env override).
if [ -n "${PARASTOO_DNS_BIND_IP:-}" ] \
   && ! ip -4 -o addr show scope global 2>/dev/null | grep -qw "$PARASTOO_DNS_BIND_IP"; then
  _BIND_FALLBACK="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 \
      | grep -vE '^(10\.|127\.|169\.254\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.)' \
      | head -n1 || true)"
  if [ -n "${_BIND_FALLBACK:-}" ]; then
    warn "DNS bind IP ${PARASTOO_DNS_BIND_IP} is not a local interface address — using ${_BIND_FALLBACK} instead."
    PARASTOO_DNS_BIND_IP="${_BIND_FALLBACK}"
  fi
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
# --- v0.3.30: CA انتخابی + EAB قابل استفادهٔ مجدد ZeroSSL (خالی برای لت‌انکریپت) ---
PARASTOO_ACME_CA=${ACME_CA}
PARASTOO_ACME_EAB_KID=${PARASTOO_ACME_EAB_KID:-}
PARASTOO_ACME_EAB_HMAC=${PARASTOO_ACME_EAB_HMAC:-}
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

# --- آپدیت از گیت‌هاب (v0.5.13): پیش‌فرض true — تایمر در §6f پس از سلامتِ استک مسلح می‌شود ---
# خاموش‌کردن: sudo parastoo autoupdate off
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

# --- sanctions (optional; docs/SANCTIONS.fa.md) ---
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

awk -v block="$REDIRECT_BLOCK" -v email="$ACME_EMAIL" \
    -v ca="${ACME_CASERVER_BLOCK:-}" -v eab="${ACME_EAB_BLOCK:-}" '
  { gsub(/__WEB_REDIRECT__/, block); gsub(/__ACME_EMAIL__/, email);
    gsub(/__ACME_CASERVER__/, ca); gsub(/__ACME_EAB__/, eab);
    gsub(/__ACME_RESOLVERS__/, "") }
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

# ---------- 4b) CLI + timers ----------
# فرمان مالک (2026-11-09): «بروزرسانی پرستو و پل باید اتوماتیک باشد و نباید
# پرسیده شود» — قرار پیشین v0.5.12 («استقرار فقط با فرمان صریح») برای
# «به‌روزرسانی» لغو می‌شود؛ نصب/حذف/نصب مجدد همچنان فقط با فرمان صریح کاربر است.
# تایمر آپدیت خودکار اینجا هنوز مسلح نمی‌شود (درس LESSONS: وسطِ نصبِ نیمه‌کپی
# نباید تیک بخورد) — مسلح‌سازی در §6f، پس از سلامتِ کاملِ استک انجام می‌شود.
# خاموش‌کردن بعداً: sudo parastoo autoupdate off
# تایمر tls-store هم می‌ماند: آینهٔ پشتیبانِ گواهی است، نه ناظر.
mark "4b) CLI + timers"
cp -f bin/parastoo /usr/local/bin/parastoo
chmod +x /usr/local/bin/parastoo
echo "$INSTALL_DIR" > /etc/parastoo-install-path
ok "parastoo CLI installed (status/logs/update/autoupdate/backup)."
if command -v systemctl >/dev/null 2>&1; then
  mkdir -p /etc/systemd/system
  systemctl daemon-reload 2>/dev/null || true
else
  warn "systemd not available; enable the TLS store mirror manually (deploy/systemd/parastoo-tls-store.timer)."
fi

# ---------- 4c) persistent TLS store — the certificate law (v0.3.32) ----------
# Owner decree (2026-11-07): the server's certificates live at a FIXED address
# (/var/lib/parastoo/tls) and are NEVER deleted — not by reinstalls, not by
# factory reset, not by tests. BEFORE requesting anything, the app checks that
# address: valid certificates there are reused as-is (restored into the
# traefik_certs volume so Traefik serves them); only genuinely missing ones
# are requested from the CA. This is what keeps repeated fresh installs from
# burning Let's Encrypt rate limits (50 certs/7d per registered domain; 5 per
# exact name set — every reinstall used to re-request everything and hit 429).
# Replaces the v0.3.20 insurance that backed up a dead path (config/letsencrypt
# — the real storage is the volume); the store now mirrors the volume every
# 10 min (parastoo-tls-store.timer) plus right here at install time. Off-server
# copies remain the agent's job (docs/AI_AGENT_LOOP.fa.md §0).
mark "4c) persistent TLS store (restore -> save)"
bash "$INSTALL_DIR/scripts/tls-store.sh" ensure || true
bash "$INSTALL_DIR/scripts/tls-store.sh" save || true
# legacy adoption: installs older than the volume design kept acme.json here
if [ -s "$INSTALL_DIR/config/letsencrypt/acme.json" ]; then
  PARASTOO_TLS_LIVE="$INSTALL_DIR/config/letsencrypt/acme.json" \
    bash "$INSTALL_DIR/scripts/tls-store.sh" save \
    && ok "Legacy acme.json adopted into the persistent store."
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
  # Field lesson (v0.3.20): `up` can exit non-zero while the stack is still
  # converging (health-check race) — in the field every container ended up
  # healthy and the deploy record was already written while the installer
  # died with "Stack failed to start". The exit code alone is NOT the truth:
  # probe the real path before declaring failure.
  warn "docker compose up returned an error — probing whether the stack converged anyway..."
  docker compose up -d >/dev/null 2>&1 || true   # one plain retry (transient build/pull errors)
  UP_PROBE=""
  for _i in 1 2 3 4 5 6 7 8; do
    if docker compose exec -T core curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then UP_PROBE="ok"; break; fi
    sleep 3
  done
  if [ "$UP_PROBE" = "ok" ]; then
    warn "compose exited non-zero, but core answers /healthz — health-check race; continuing (docs/LESSONS.fa.md)."
  else
    echo ""
    warn "The auto-update timer is already armed — pushing the fix to GitHub deploys it automatically."
    say "Diagnose the failing container:"
    say "  cd $INSTALL_DIR && sudo docker compose ps"
    say "  sudo docker logs parastoo-<name> --tail 60   # e.g. parastoo-gitea"
    say "  sudo bash $INSTALL_DIR/scripts/doctor.sh"
    die "Stack failed to start (verified: core does not answer /healthz). If the error is about image pulls:
  1) build the offline bundle: bash scripts/fetch-images.sh (on an unsanctioned machine) -> put the offline-images/ folder here -> run install.sh again
  2) or set a healthy mirror: PARASTOO_REGISTRY_MIRRORS=\"https://docker.arvancloud.ir\" sudo -E bash install.sh
  3) if the error mentions pypi.org / files.pythonhosted.org: set a pip mirror and re-run:
     PARASTOO_PIP_INDEX_URL=\"https://package-mirror.liara.ir/repository/pypi/simple\" sudo -E bash install.sh
  Full guide: docs/SANCTIONS.fa.md"
  fi
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
# v0.3.29 field lesson (2026-11-06, 4th fresh install): with TLS enabled the
# web entrypoint redirects EVERYTHING http->https (301), so a :80-only probe
# that accepts only 200 raised a scary false alarm on every healthy install.
# Now: 200 passes; 301/302/307/308 = routed, so verify the REAL user path on
# :443 (curl -k: the LE certificate may not be issued yet at install time).
mark "6b) panel via traefik"
PANEL_CODE=""
for i in $(seq 1 10); do
  PANEL_CODE="$(timeout 8 curl -sS -o /dev/null -w '%{http_code}' -H "Host: $PANEL_DOMAIN" http://127.0.0.1/healthz 2>/dev/null || true)"
  case "$PANEL_CODE" in
    200) break ;;
    301|302|307|308)
      H_CODE="$(timeout 8 curl -skS -o /dev/null -w '%{http_code}' --resolve "$PANEL_DOMAIN:443:127.0.0.1" "https://$PANEL_DOMAIN/healthz" 2>/dev/null || true)"
      if [ "$H_CODE" = "200" ]; then PANEL_CODE=200; break; fi
      ;;
  esac
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
  elif docker logs parastoo-traefik --tail 200 2>&1 | grep -q 'rateLimited'; then
    # v0.3.29 — say the truth instead of "fault in the Traefik layer":
    # LE rate limit (429) after many reinstalls of the same domain in 7 days.
    RETRY_AT="$(docker logs parastoo-traefik --tail 200 2>&1 | grep -o 'retry after [0-9][0-9-]* [0-9:]* UTC' | tail -1 | sed 's/retry after //')"
    warn "Root cause found: Let's Encrypt rate limit (429) — too many certificates were issued for this exact domain in the last 7 days (typical after repeated reinstalls)."
    say "Nothing is broken: certificates will be issued automatically after ${RETRY_AT:-the rate-limit window}. The core worker retries ACME every 10 minutes — no action needed."
  fi
fi

# ---------- 6c) authoritative DNS — the silent-kill check (v0.3.25) ----------
# Field failure (2026-11-06): parastoo-dns reported "healthy" while its host
# port never bound (a NAT egress IP in PARASTOO_DNS_BIND_IP) — authoritative
# DNS was dead with every container green, and panel/git answered only from
# stale resolver caches. Probe the REAL host path: the container must own a
# :53 host binding; a green container with none is a failure.
mark "6c) dns host bind"
if [ -n "$PARASTOO_DNS_BIND_IP" ]; then
  DNS_PORT_OK=""
  for i in $(seq 1 6); do
    if docker port parastoo-dns 2>/dev/null | grep -q ':53'; then DNS_PORT_OK="ok"; break; fi
    sleep 2
  done
  if [ "$DNS_PORT_OK" = "ok" ]; then
    ok "Authoritative DNS is bound on the host port 53."
  else
    warn "parastoo-dns has NO host port binding on :53 — authoritative DNS would be silently dead."
    say "Diagnose:  sudo docker port parastoo-dns   (empty output = not bound)"
    say "PARASTOO_DNS_BIND_IP in .env must be an interface address of this server:"
    say "  $(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | paste -sd' ' -)"
    say "Fix: edit .env, then:  cd $INSTALL_DIR && sudo docker compose up -d dns"
    die "DNS host bind failed — a green dns container without a host :53 binding kills every domain."
  fi
fi

# ---------- 6d) provision the base zone + apply the domain (v0.3.25) ----------
# Field gap (2026-11-06): with an explicit PARASTOO_BASE_DOMAIN the installer
# prints https://panel.<domain>/setup, but nothing ever created the DNS zone
# on our PowerDNS — the printed URL did not resolve ANYWHERE, and the zone is
# created only by the very wizard that URL opens (chicken-and-egg; nip.io
# installs never noticed because the wildcard resolves). Provision the base
# zone and run the same apply_domain the wizard uses, right here, so
# panel/git/*.apps resolve (and ACME can start) the moment the installer ends.
mark "6d) base zone + apply domain"
case "$BASE_DOMAIN" in
  *.nip.io)
    ok "nip.io domain — zone provisioning and HTTPS activation happen in the wizard."
    ;;
  *)
    ZONE_OUT="$(docker compose exec -T \
      -e PZ_BASE="$BASE_DOMAIN" -e PZ_EMAIL="$ACME_EMAIL" -e PZ_TLS="$TLS_ENABLED" \
      core python3 -c '
import os
from app.db import SessionLocal
from app.services import dnsops
from app.traefik_ops import apply_domain
db = SessionLocal()
try:
    base = os.environ["PZ_BASE"].strip().lower().rstrip(".")
    try:
        dnsops.get_zone(base)
        print("zone:exists")
    except dnsops.DnsError as e:
        if e.status == 404:
            dnsops.provision_zone(base, db=db, kind="base")
            print("zone:provisioned")
        else:
            print("zone:check_failed:" + str(e.status))
    except dnsops.DnsUnavailable:
        print("zone:dns_unavailable")
    r = apply_domain(db, base, os.environ["PZ_EMAIL"], os.environ["PZ_TLS"] == "true")
    print("apply:files=" + str(r.get("files")) + " zone_sync=" + str(r.get("zone_sync")))
    for err in r.get("errors", []):
        print("apply_error:" + err)
finally:
    db.close()
' 2>&1 || true)"
    echo "$ZONE_OUT" | sed 's/^/        /'
    case "$ZONE_OUT" in
      *zone:provisioned*|*zone:exists*)
        ok "Base zone ${BASE_DOMAIN} is served by our authoritative DNS — panel/git/apps resolve now."
        ;;
      *)
        warn "Base zone could not be provisioned automatically (see lines above)."
        warn "If https://${PANEL_DOMAIN} does not resolve, provision ${BASE_DOMAIN} on the DNS page of the panel and re-run the domain step in the wizard."
        ;;
    esac
    ;;
esac

# ---------- 6e) TLS store — seed a fresh volume, then sync (v0.3.32) ----------
# After a factory-reset the volume is brand-new and empty: ensure() restores
# the store into it and restarts Traefik BEFORE/instead of ACME re-requesting
# every hostname (the exact 429 that closed installs 4–6 of this domain).
mark "6e) tls store sync"
bash "$INSTALL_DIR/scripts/tls-store.sh" ensure || true
bash "$INSTALL_DIR/scripts/tls-store.sh" save || true

# ---------- 6f) auto-update timer — DEFAULT ON (owner decree 2026-11-09) ----------
# «بروزرسانی پرستو و پل باید اتوماتیک باشد و نباید پرسیده شود.» استک اکنون
# سالم و بالا است و مهر .parastoo-deploy نوشته شده؛ اولین تیکِ تایمر پس از
# اینجا می‌آید و اگر کامیت تازه‌ای نباشد بی‌کار برمی‌گردد. خاموش‌کردن:
#   sudo parastoo autoupdate off
if command -v systemctl >/dev/null 2>&1; then
  mkdir -p /etc/systemd/system
  for _u in parastoo-stack.service parastoo-autoupdate.service parastoo-autoupdate.timer \
            parastoo-tls-store.service parastoo-tls-store.timer; do
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "deploy/systemd/$_u" > "/etc/systemd/system/$_u" 2>/dev/null || true
  done
  systemctl daemon-reload 2>/dev/null || true
  # v0.5.27 — آمادگی ری‌استارت (فرمان مالک): یونیت همگرایی بوت استک باید
  # فعال باشد تا «بالا آمدن پرستو بعد از ری‌استارت» به بازیابیِ per-container
  # داکر یا تایمر auto-update وابسته نباشد.
  systemctl enable parastoo-stack.service >/dev/null 2>&1 \
    && ok "Boot convergence enabled (parastoo-stack.service: docker compose up --wait after docker.service at every boot)." \
    || warn "Could not enable parastoo-stack.service — after a reboot run: cd $INSTALL_DIR && sudo docker compose up -d --wait"
  systemctl enable --now parastoo-tls-store.timer 2>/dev/null \
    && ok "TLS store sync timer enabled (certificates mirror to /var/lib/parastoo/tls every 10 min)." \
    || warn "Could not enable parastoo-tls-store.timer (certificates still saved at install time)."
  systemctl enable --now parastoo-autoupdate.timer 2>/dev/null \
    && ok "Auto-update timer armed — Parastoo now tracks the GitHub channel on its own (opt-out: sudo parastoo autoupdate off)." \
    || warn "Could not enable parastoo-autoupdate.timer (manual update still works: sudo parastoo update)."
  # v0.5.27 — بررسی بلند داکر: اگر داکر enable نباشد، بعد از ری‌استارت هیچ
  # کانتینری برنمی‌گردد؛ بی‌صدا رد شدنش (|| true قدیمی) این حقیقت را می‌پوشاند.
  if ! systemctl is-enabled docker >/dev/null 2>&1; then
    warn "docker.service is NOT enabled — the stack will NOT come back after a reboot!"
    warn "Fix now:  sudo systemctl enable docker"
  fi
else
  warn "systemd not available; auto-update timer not armed (manual update: sudo parastoo update)."
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
echo "  Updates:  automatic — the server tracks the GitHub channel on its own (owner decree 2026-11-09); opt-out: sudo parastoo autoupdate off"
echo "  Version API:  ${SCHEME}://${PANEL_DOMAIN}/api/version"
echo "  Sanctions & offline bundle:  docs/SANCTIONS.fa.md"
echo ""
