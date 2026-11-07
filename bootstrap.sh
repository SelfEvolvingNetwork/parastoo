#!/usr/bin/env bash
# =============================================================
#  Parastoo — one-command bootstrap from GitHub (SSH-safe output)
#
#    curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash
#
#  Fetches the package from GitHub (in order):
#    REF=main (default): 1) repo tarball (codeload = live main)
#                        2) release zip (fallback; may lag main)
#                        3) git clone
#    REF=<tag> (pinned): 1) release zip  2) repo tarball  3) git clone
#  v0.3.28 field lesson: the v0.3.27 release zip was cut BEFORE the
#  balance-guard commit landed on main — fresh installs silently ran old
#  code while /api/version claimed the newest sha (release zip is only
#  guaranteed fresh for its own tag; main installs must track main).
#  Extracts it itself and hands the extracted folder to setup.sh,
#  which installs to /opt/parastoo and runs install.sh.
#  (In `curl | bash` mode nothing exists on disk; everything flows
#  through this script — so all paths handed over are absolute.)
#
#  Optional variables (set before the command):
#    PARASTOO_REF=v0.3.22       branch or tag (default: main) — prefer a stable tag
#    PARASTOO_GH_PROXY=URL      GitHub proxy prefix for Iran, e.g.:
#                               https://gh-proxy.com/
#    PARASTOO_ZIP_NAME=...      release zip filename (default: parastoo-v0.4.3.zip; zip is
#                               the primary source only for a pinned tag install)
#    All other PARASTOO_* variables (BASE_DOMAIN, ...) pass through to install.sh.
#
#  CRITICAL (v0.3.24): pass variables with `sudo env VAR=x bash` — the shorter
#  `sudo VAR=x bash` form is silently stripped by default sudoers (env_reset),
#  and the install then runs with the default nip.io domain instead of yours.
#
#  If you already cloned the repo, from inside that folder just run:
#    sudo bash bootstrap.sh
#
#  NOTE: installer output is English on purpose — Persian/RTL text
#  breaks rendering over SSH. The web panel itself stays Persian.
# =============================================================
set -euo pipefail

ORG="${PARASTOO_GH_ORG:-SelfEvolvingNetwork}"
REPO="${PARASTOO_GH_REPO:-parastoo}"
REF="${PARASTOO_REF:-main}"
PROXY="${PARASTOO_GH_PROXY:-}"
ZIP_NAME="${PARASTOO_ZIP_NAME:-parastoo-v0.4.3.zip}"
RAW_URL="${PROXY}https://raw.githubusercontent.com/${ORG}/${REPO}/${REF}/bootstrap.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()  { echo -e "${BLUE}==>${NC} $*"; }
ok()   { echo -e "${GREEN} [ok]${NC} $*"; }
warn() { echo -e "${YELLOW} [warn]${NC} $*"; }
die()  { echo -e "${RED} error:${NC} $*" >&2; exit 1; }

# ---------- 1) root (compatible with curl | bash) ----------
is_file_self() {
  local s="${BASH_SOURCE[0]:-}"
  [ -n "$s" ] && [ -f "$s" ] && [ "$s" != "bash" ] && [ "$s" != "sh" ] && [ "$s" != "/dev/stdin" ]
}
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    if is_file_self; then
      exec sudo -E bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
    fi
    # pipe mode: re-fetch this file as root and run it
    say "Re-fetching the script with root privileges..."
    exec sudo -E bash -c "curl -fsSL '$RAW_URL' | bash"
  fi
  die "Run as root:  curl -fsSL $RAW_URL | sudo bash"
fi

# ---------- 2) package already here (manual clone / extracted zip) ----------
if [ -f ./install.sh ] && [ -f ./docker-compose.yml ] && [ -f ./setup.sh ]; then
  say "Parastoo package found in current directory; running directly..."
  exec bash setup.sh
fi

# ---------- 3) fetch dependencies ----------
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
  say "Installing curl..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null
fi
fetch() { # fetch <url> <out>
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 2 --max-time 600 -o "$2" "$1"
  else
    wget -q -O "$2" "$1"
  fi
}

WORK="$(mktemp -d /tmp/parastoo-bootstrap.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---------- 4) fetch package from GitHub — three routes ----------
say "Fetching Parastoo from GitHub: ${ORG}/${REPO} (ref: ${REF})"

got_zip() { fetch "${PROXY}https://github.com/${ORG}/${REPO}/releases/latest/download/${ZIP_NAME}" "$WORK/pkg.zip" && [ -s "$WORK/pkg.zip" ]; }
got_tar() { fetch "${PROXY}https://codeload.github.com/${ORG}/${REPO}/tar.gz/${REF}" "$WORK/pkg.tar.gz" && [ -s "$WORK/pkg.tar.gz" ]; }
got_clone() {
  command -v git >/dev/null 2>&1 || {
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1 || return 1
  }
  git clone --depth 1 --branch "$REF" "${PROXY}https://github.com/${ORG}/${REPO}.git" "$WORK/repo" 2>/dev/null
}

MODE=""
# v0.3.28: for main, the tarball IS the channel (always == live main); the
# release zip is the fallback because it may lag main (and for a pinned tag
# the zip is the primary, stable artifact).
if [ "$REF" = "main" ]; then
  if got_tar; then
    MODE="tar"; ok "Repository tarball downloaded (ref: ${REF} — live main)."
  elif got_zip; then
    MODE="zip"; ok "Release package downloaded (${ZIP_NAME} — may lag main)."
  elif got_clone; then
    MODE="git"; ok "Repository cloned (ref: ${REF})."
  fi
else
  if got_zip; then
    MODE="zip"; ok "Release package downloaded (${ZIP_NAME})."
  elif got_tar; then
    MODE="tar"; ok "Repository tarball downloaded (ref: ${REF})."
  elif got_clone; then
    MODE="git"; ok "Repository cloned (ref: ${REF})."
  fi
fi
if [ -z "$MODE" ]; then
  die "Could not fetch from GitHub.
Alternative routes:
  1) proxy (Iran):  curl -fsSL ${PROXY}https://gh-proxy.com/https://raw.githubusercontent.com/${ORG}/${REPO}/${REF}/bootstrap.sh | sudo bash
  2) manual:  git clone https://github.com/${ORG}/${REPO}.git && cd ${REPO} && sudo bash setup.sh
  3) grab the zip from the releases page, copy it to the server, then:  sudo bash setup.sh parastoo-v0.4.3.zip"
fi

# ---------- 5) extract package and hand the FOLDER to setup.sh ----------
# Important: in `curl | bash` mode there is no setup.sh in the CWD;
# we extract the package here and pass the absolute folder path to
# setup.sh inside that folder (it installs to /opt/parastoo).
if [ "$MODE" = "git" ]; then
  SRC_DIR="$WORK/repo"
else
  say "Extracting package..."
  EXTRACT_DIR="$WORK/src"
  mkdir -p "$EXTRACT_DIR"
  case "$MODE" in
    zip) if command -v unzip >/dev/null 2>&1; then unzip -q "$WORK/pkg.zip" -d "$EXTRACT_DIR"
         else python3 -m zipfile -e "$WORK/pkg.zip" "$EXTRACT_DIR"; fi ;;
    tar) tar -xzf "$WORK/pkg.tar.gz" -C "$EXTRACT_DIR" ;;
  esac
  if [ -f "$EXTRACT_DIR/install.sh" ]; then
    SRC_DIR="$EXTRACT_DIR"
  else
    SRC_DIR="$(find "$EXTRACT_DIR" -maxdepth 2 -name install.sh -printf '%h\n' 2>/dev/null | head -n1 || true)"
  fi
fi
[ -n "${SRC_DIR:-}" ] && [ -f "$SRC_DIR/setup.sh" ] && [ -f "$SRC_DIR/install.sh" ] \
  || die "Invalid package (setup.sh/install.sh not found)."
ok "Package verified; running installer from ${SRC_DIR}..."

# ---------- 6) resolve the exact commit OF WHAT WAS INSTALLED (best effort) ----------
# install.sh records it in .env + .parastoo-deploy (/api/version shows it) and
# the auto-updater compares against it. v0.3.28 field lesson: zip installs must
# NEVER claim main's live HEAD — the v0.3.27 zip was cut before the balance-guard
# commit, yet recorded main's sha and masked the staleness. Since v0.3.28 the
# release zip carries its own build stamp (.release-commit); tarball/clone modes
# are live main by construction, so ls-remote is honest for them.
COMMIT=""
if [ "$MODE" = "git" ]; then
  COMMIT="$(git -C "$WORK/repo" rev-parse HEAD 2>/dev/null || true)"
elif [ "$MODE" = "zip" ]; then
  if [ -f "${SRC_DIR}/.release-commit" ]; then
    COMMIT="$(head -c 40 "${SRC_DIR}/.release-commit" 2>/dev/null | tr -cd '0-9a-f' || true)"
    [ -n "$COMMIT" ] && ok "Release stamp honored: ${COMMIT:0:12} (from the package itself)"
  fi
  if [ -z "$COMMIT" ]; then
    warn "Zip has no .release-commit stamp — recording 'unknown'; the auto-updater will pin the real sha on its first tick."
  fi
else
  if command -v git >/dev/null 2>&1; then
    COMMIT="$(timeout 25 git ls-remote "${PROXY}https://github.com/${ORG}/${REPO}.git" \
                "refs/heads/${REF}" "refs/tags/${REF}" 2>/dev/null | awk 'NR==1{print $1}' || true)"
  fi
  if [ -z "$COMMIT" ]; then
    COMMIT="$(timeout 15 curl -fsSL --max-time 12 \
                "${PROXY}https://api.github.com/repos/${ORG}/${REPO}/commits/${REF}" 2>/dev/null \
              | sed -n 's/.*"sha" *: *"\([0-9a-f]\{40\}\)".*/\1/p' | head -n1 || true)"
  fi
fi
if [ -n "$COMMIT" ]; then
  export PARASTOO_DEPLOYED_COMMIT="$COMMIT"
  ok "Deployed commit: ${COMMIT:0:12} (recorded by the installer)"
else
  warn "Commit SHA not resolved (non-fatal); the auto-updater will record it on its first tick."
fi

exec bash "$SRC_DIR/setup.sh" "$SRC_DIR"
