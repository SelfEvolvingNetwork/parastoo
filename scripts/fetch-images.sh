#!/usr/bin/env bash
# =============================================================
#  Parastoo — offline image bundle builder (the reliable sanctions route)
#
#  When? When the Iran server cannot reach any mirror, or you want a
#  100% offline install with zero foreign internet access.
#
#  How? Run this script on "an unsanctioned machine" (a personal laptop
#  with a changed IP, a foreign server, or GitHub Actions):
#
#      docker version           # docker is required
#      bash scripts/fetch-images.sh
#
#  Output: offline-images/parastoo-images.tar.gz
#  Copy that folder next to the Parastoo package on the Iran server
#  (scp) and run install.sh again — images load from the local bundle;
#  no Docker Hub pull happens at all.
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
IMAGES_FILE="deploy/images.txt"
OUT_DIR="${1:-offline-images}"

command -v docker >/dev/null 2>&1 || { echo "error: docker is not installed."; exit 1; }

# --- read the image list (non-empty, non-comment lines) ---
LIST=()
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '[:space:]')"
  [ -z "$line" ] && continue
  LIST+=("$line")
done < "$IMAGES_FILE"

[ ${#LIST[@]} -gt 0 ] || { echo "error: $IMAGES_FILE is empty."; exit 1; }

echo "==> pulling ${#LIST[@]} images for linux/amd64 ..."
for img in "${LIST[@]}"; do
  echo "    docker pull --platform linux/amd64 $img"
  docker pull --platform linux/amd64 "$img"
done

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/parastoo-images.tar.gz"
echo "==> building archive: $ARCHIVE"
docker save "${LIST[@]}" | gzip > "$ARCHIVE"

echo ""
echo "  done: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
echo "  next step: copy the '$OUT_DIR' folder next to the Parastoo package on the server (scp) and run sudo bash install.sh again."
