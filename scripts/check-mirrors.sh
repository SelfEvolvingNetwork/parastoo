#!/usr/bin/env bash
# =============================================================
#  Parastoo — Docker Hub mirror tester, run from the server itself
#
#  Probes each candidate mirror (deploy/mirrors.txt) via the standard
#  /v2/ endpoint: a 200/301/302/401 response means the registry is
#  alive. Dead mirrors and too-slow ones are dropped; the rest are
#  printed ordered by speed.
#
#  Usage:
#      bash scripts/check-mirrors.sh                 # report only
#      bash scripts/check-mirrors.sh --apply PATH    # write live ones into daemon.json
#  Stdout: live mirror URLs, one per line (fastest first) — consumed by install.sh
# =============================================================
set -euo pipefail

cd "$(dirname "$0")/.."
MIRRORS_FILE="${PARASTOO_MIRRORS_FILE:-deploy/mirrors.txt}"
TIMEOUT="${PARASTOO_MIRROR_TIMEOUT:-6}"
MAX_LATENCY="${PARASTOO_MIRROR_MAX_LATENCY:-4}"   # seconds; above this = too slow
APPLY=""

[ "${1:-}" = "--apply" ] && { APPLY="${2:?daemon.json path?}"; }

command -v curl >/dev/null 2>&1 || { echo "error: curl is not installed." >&2; exit 2; }

RESULTS="$(mktemp)"
trap 'rm -f "$RESULTS"' EXIT

alive() {
  # HTTP status on /v2/ (following redirects) + response time
  local url="$1" code latency
  code="$(curl -sSL -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$url/v2/" 2>/dev/null || echo 000)"
  latency="$(curl -sSL -o /dev/null -w '%{time_total}' --max-time "$TIMEOUT" "$url/v2/" 2>/dev/null || echo 999)"
  case "$code" in
    200|301|302|401) : ;;
    *) return 1 ;;
  esac
  # too slow = practically useless
  awk -v l="$latency" -v m="$MAX_LATENCY" 'BEGIN{exit !(l+0 <= m+0)}' || return 1
  printf '%.2f %s\n' "$latency" "$url"
}

n_total=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%#*}"
  line="$(echo "$line" | tr -d '[:space:]')"
  [ -z "$line" ] && continue
  n_total=$((n_total + 1))
  printf '    testing %-46s ' "$line" >&2
  if alive "$line" >> "$RESULTS"; then
    echo "ok" >&2
  else
    echo "dead" >&2
  fi
done < "$MIRRORS_FILE"

SORTED="$(sort -n "$RESULTS" | awk '{print $2}')"
N_ALIVE="$(echo -n "$SORTED" | grep -c . || true)"

if [ "$N_ALIVE" -eq 0 ]; then
  echo "no live mirror found." >&2
  exit 3
fi

echo "" >&2
echo "  live mirrors (fastest first): $N_ALIVE of $n_total" >&2
echo "$SORTED" | sed 's/^/    + /' >&2

if [ -n "$APPLY" ]; then
  # shellcheck disable=SC2086
  python3 scripts/merge-docker-mirrors.py "$APPLY" $SORTED >/dev/null
fi

# stdout: only the URLs (consumed by install.sh)
echo "$SORTED"
