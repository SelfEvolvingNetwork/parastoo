#!/usr/bin/env bash
# =============================================================
#  Parastoo — one-command setup, from zero to a running panel
#
#  Usage modes:
#
#    1) zip file next to this script (or inside current folder):
#         sudo bash setup.sh
#
#    2) zip path or filename:
#         sudo bash setup.sh parastoo-v0.2.4.zip
#
#    3) direct download from a URL (zip or tar.gz):
#         sudo bash setup.sh https://example.com/parastoo-v0.2.4.zip
#
#    3b) already-extracted package folder (bootstrap.sh calls it this way):
#         sudo bash setup.sh /path/to/parastoo
#
#    * Recommended route — automatic bootstrap from GitHub (bootstrap.sh):
#         curl -fsSL https://raw.githubusercontent.com/SelfEvolvingNetwork/parastoo/main/bootstrap.sh | sudo bash
#
#    4) classic curl | bash (host setup.sh somewhere):
#         curl -fsSL https://example.com/setup.sh | bash -s -- https://example.com/parastoo-v0.2.4.zip
#
#    5) if you already extracted the package, run it right here —
#       it installs from that folder directly:
#         sudo bash setup.sh
#
#  Reinstall = update: /opt/parastoo gets the new code; .env, Traefik
#  config, offline images and backups are preserved, and Docker volumes
#  (your data) are untouched.
#
#  NOTE: output is English on purpose — Persian/RTL text breaks over
#  SSH. The web panel itself stays Persian.
# =============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say()   { echo -e "${BLUE}==>${NC} $*"; }
ok()    { echo -e "${GREEN} [ok]${NC} $*"; }
warn()  { echo -e "${YELLOW} [warn]${NC} $*"; }
die()   { echo -e "${RED} error:${NC} $*" >&2; exit 1; }

INSTALL_ROOT="/opt/parastoo"
SRC="${1:-}"
SELF="${BASH_SOURCE[0]:-}"

[ "$(id -u)" -eq 0 ] || die "Run with sudo:  sudo bash setup.sh"

# ---------- 1) resolve source: local zip, URL, or extracted folder ----------
is_url()  { case "$1" in http://*|https://*) return 0 ;; *) return 1 ;; esac; }
self_dir() { [ -f "$SELF" ] && dirname "$(readlink -f "$SELF")" || echo ""; }

# if this script lives inside an extracted package, install.sh there is enough
DIRECT_DIR=""
if [ -f "$SELF" ]; then
  d="$(self_dir)"
  [ -f "$d/docker-compose.yml" ] && [ -f "$d/install.sh" ] && DIRECT_DIR="$d"
fi

latest_zip() {  # newest parastoo*.zip among the given globs
  local best="" best_t=0 t pat f
  for pat in "$@"; do
    for f in $pat; do          # intentional glob expansion
      [ -f "$f" ] || continue
      t="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
      if [ "$t" -ge "$best_t" ]; then best="$f"; best_t="$t"; fi
    done
  done
  echo "$best"
}

ZIP_PATH=""
if [ -n "$SRC" ]; then
  if is_url "$SRC"; then
    MODE="url"
  elif [ -d "$SRC" ] && [ -f "$SRC/install.sh" ]; then
    SRC_DIR="$(readlink -f "$SRC")"; MODE="dir"      # extracted package folder
  elif [ -f "$SRC" ]; then
    ZIP_PATH="$(readlink -f "$SRC")"; MODE="zip"
  else
    die "File or folder not found: $SRC"
  fi
elif [ -n "$DIRECT_DIR" ]; then
  MODE="direct"
else
  sd="$(self_dir)"
  ZIP_PATH="$(latest_zip "${sd:+$sd/parastoo*.zip}" "$PWD/parastoo*.zip" 2>/dev/null || true)"
  [ -n "$ZIP_PATH" ] && MODE="zip" || { MODE="none"; }
fi

# ---------- 2) direct run on an extracted package ----------
if [ "${MODE:-}" = "direct" ]; then
  say "Parastoo package is right here; running the installer..."
  cd "$DIRECT_DIR"
  exec bash install.sh
fi

if [ "${MODE:-}" = "none" ]; then
  die "No package source found.
Usage:
  sudo bash setup.sh <zip-file>
  sudo bash setup.sh https://example.com/parastoo-v0.2.4.zip
  sudo bash setup.sh /path/to/extracted/package
Or place this script next to the zip file and simply run:  sudo bash setup.sh"
fi

# ---------- 3) dependencies: curl / unzip (python3 fallback) ----------
if [ "${MODE:-}" != "dir" ]; then
  say "Checking dependencies..."
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null
  fi
  if ! command -v unzip >/dev/null 2>&1; then
    apt-get install -y -qq unzip >/dev/null 2>&1 || true
  fi
  EXTRACTOR="unzip"
  command -v unzip >/dev/null 2>&1 || EXTRACTOR="python3"   # Ubuntu always ships python3
fi

WORK="$(mktemp -d /tmp/parastoo-setup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# ---------- 4) fetch the package (dir mode skips this) ----------
if [ "${MODE:-}" = "dir" ]; then
  WORK_SRC="$SRC_DIR"
  ok "Package folder: $WORK_SRC"
else
  # extension is preserved so GitHub tarballs work too (pkg.zip or pkg.tar.gz)
  case "${MODE}:${SRC:-}" in
    url:*.tar.gz|url:*.tgz) PKG="$WORK/pkg.tar.gz" ;;
    url:*)                  PKG="$WORK/pkg.zip" ;;
    *)                      PKG="$WORK/pkg.tar.gz"; case "$ZIP_PATH" in *.tar.gz|*.tgz) ;; *) PKG="$WORK/pkg.zip" ;; esac ;;
  esac

  if [ "$MODE" = "url" ]; then
    say "Downloading package from: $SRC"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --retry 2 --max-time 900 -o "$PKG" "$SRC" \
        || die "Download failed. Check the URL or copy the file to the server manually."
    else
      wget -q -O "$PKG" "$SRC" || die "Download failed."
    fi
    ZIP_PATH="$PKG"
  else
    say "Package: $ZIP_PATH"
    cp -f "$ZIP_PATH" "$PKG"
  fi

  # ---------- 5) extract and normalize layout ----------
  say "Extracting package..."
  mkdir -p "$WORK/src"        # tar needs -C target to exist
  case "$ZIP_PATH" in
    *.tar.gz|*.tgz)
      tar -xzf "$ZIP_PATH" -C "$WORK/src"        # GitHub tarball (codeload)
      ;;
    *)
      if [ "$EXTRACTOR" = "unzip" ]; then
        unzip -q "$ZIP_PATH" -d "$WORK/src"
      else
        python3 -m zipfile -e "$ZIP_PATH" "$WORK/src"
      fi
      ;;
  esac
  # the package may have a parastoo/ root or files at top level
  if [ ! -f "$WORK/src/docker-compose.yml" ]; then
    inner="$(find "$WORK/src" -maxdepth 2 -name docker-compose.yml -printf '%h\n' 2>/dev/null | head -n1 || true)"
    [ -n "$inner" ] && WORK_SRC="$inner" || die "Invalid package (docker-compose.yml not found)."
  else
    WORK_SRC="$WORK/src"
  fi
fi
[ -f "$WORK_SRC/install.sh" ] || die "Invalid package (install.sh not found)."
ok "Package verified: $(grep -m1 -o 'v[0-9][0-9.]*' "$WORK_SRC/README.md" 2>/dev/null || echo 'Parastoo')"

# ---------- 6) install into /opt/parastoo (or safe update) ----------
if [ -f "$INSTALL_ROOT/.env" ]; then
  say "Existing installation found; safe update (new code, .env and data untouched)..."
  # only code folders are replaced; .env/config/backups/offline-images stay
  rm -rf "$INSTALL_ROOT/core" "$INSTALL_ROOT/bin" "$INSTALL_ROOT/deploy" \
         "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/postgres-init" "$INSTALL_ROOT/docs"
  cp -a "$WORK_SRC/." "$INSTALL_ROOT/"
  ok "Parastoo code updated."
else
  say "Fresh install into $INSTALL_ROOT ..."
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  rm -rf "$INSTALL_ROOT"
  cp -a "$WORK_SRC" "$INSTALL_ROOT"
fi
chmod +x "$INSTALL_ROOT/install.sh" "$INSTALL_ROOT/uninstall.sh" \
         "$INSTALL_ROOT/scripts/"*.sh "$INSTALL_ROOT/bin/parastoo" 2>/dev/null || true

# ---------- 7) run the main installer (docker, mirrors, stack, CLI) ----------
cd "$INSTALL_ROOT"
exec bash install.sh
