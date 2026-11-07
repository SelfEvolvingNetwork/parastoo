#!/usr/bin/env bash
# =============================================================
#  Parastoo persistent TLS store (v0.3.32) — the certificate law
#
#  Owner decree (2026-11-07): the server's certificates live at a FIXED
#  address and are NEVER deleted — not by reinstalls, not by factory
#  reset, not by tests. The rule has two halves:
#
#    1) STORE (this script): the canonical copy lives at
#       /var/lib/parastoo/tls/acme.json  (0700 dir, 0600 file, root).
#       Everything Traefik issues is mirrored here; nothing may wipe it.
#
#    2) CHECK-BEFORE-REQUEST: before any ACME activity, Traefik's live
#       storage (the traefik_certs volume -> /letsencrypt/acme.json) is
#       reconciled with the store. Valid certificates in the store are
#       restored as-is; only genuinely missing certificates are ever
#       requested from the CA. This is what keeps repeated fresh
#       installs from burning Let's Encrypt rate limits (50 certs/7d
#       per registered domain; 5 per exact name set — every reinstall
#       used to re-request everything and hit 429).
#
#  Why the store is a bare host directory: docker volumes die with
#  `compose down -v` (factory-reset, uninstall) and with server
#  rebuilds; /var/lib/parastoo survives both and survives the code
#  directory too (installs may replace /opt/parastoo entirely).
#
#  Commands:
#    save     live -> store   (atomic; never stores invalid JSON;
#                              never overwrites a valid store with a
#                              broken live file)
#    restore  store -> live   (only when live is missing/empty/broken;
#                              Traefik then loads them at start)
#    ensure   restore + restart Traefik if something was restored
#                             (use right after `compose up` on a fresh
#                              volume: seeds it before ACME re-requests)
#    status   one glance at both sides
#
#  Exit codes — restore/ensure: 0 live is valid (already or restored),
#  1 nothing to restore, 2 cannot resolve the live storage.
#  save: 0 in every benign case (incl. "no live file yet"), nonzero
#  only on real I/O failure — the systemd timer runs it silently.
#
#  Testability: PARASTOO_TLS_STORE / PARASTOO_TLS_LIVE override the
#  paths (used by tests/tls_store_test.sh); PARASTOO_TRAEFIK_CONTAINER
#  overrides the container name for `ensure`.
# =============================================================
set -u

STORE_DIR="${PARASTOO_TLS_STORE:-/var/lib/parastoo/tls}"
STORE_FILE="$STORE_DIR/acme.json"
LIVE_OVERRIDE="${PARASTOO_TLS_LIVE:-}"
TRAEFIK_CONTAINER="${PARASTOO_TRAEFIK_CONTAINER:-parastoo-traefik}"

ok()   { echo "  [tls-store] ok: $*"; }
warn() { echo "  [tls-store] warn: $*"; }

# --- the live acme.json (inside the traefik_certs volume) --------------------
resolve_live() {
  if [ -n "$LIVE_OVERRIDE" ]; then
    printf '%s' "$LIVE_OVERRIDE"
    return 0
  fi
  command -v docker >/dev/null 2>&1 || return 1
  local v mp
  for v in $(docker volume ls -q 2>/dev/null | grep 'traefik_certs' || true); do
    mp="$(docker volume inspect -f '{{.Mountpoint}}' "$v" 2>/dev/null || true)"
    if [ -n "$mp" ] && [ -d "$mp" ]; then
      printf '%s/acme.json' "$mp"
      return 0
    fi
  done
  return 1
}

# --- validity: parseable JSON with an account and/or certificates -----------
_json_valid() {  # 0 = usable for restore
  python3 - "$1" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
if not isinstance(d, dict) or not d:
    sys.exit(1)
for v in d.values():
    if isinstance(v, dict) and (v.get("Account") or v.get("certificates")):
        sys.exit(0)
sys.exit(1)
PY
}

_json_cert_count() {
  python3 - "$1" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(sum(len(v.get("certificates") or []) for v in d.values() if isinstance(v, dict)))
except Exception:
    print("?")
PY
}

# --- atomic copy: tmp in the target dir, 0600, rename ------------------------
_put() {  # _put SRC DST
  local src="$1" dst="$2" tmp
  tmp="$(dirname "$dst")/.acme.$(date +%s%N).tmp"
  cat "$src" > "$tmp" && chmod 600 "$tmp" && mv -f "$tmp" "$dst"
}

cmd_save() {
  local live rc
  live="$(resolve_live)" || { warn "no live traefik_certs volume found yet (fresh install?)"; return 0; }
  if [ ! -s "$live" ]; then
    warn "live acme.json is empty at $live (certificates not issued yet) — store untouched"
    return 0
  fi
  if ! _json_valid "$live"; then
    warn "live acme.json is not valid JSON — refusing to archive garbage; store untouched"
    return 0
  fi
  mkdir -p "$STORE_DIR" && chmod 700 "$STORE_DIR"
  if [ -s "$STORE_FILE" ] && _json_valid "$STORE_FILE" && cmp -s "$live" "$STORE_FILE"; then
    ok "store is already up to date ($(chmod 700 "$STORE_DIR"; _json_cert_count "$STORE_FILE") certificates)"
    return 0
  fi
  if [ -s "$STORE_FILE" ] && _json_valid "$STORE_FILE" \
     && [ "$(stat -c %Y "$STORE_FILE" 2>/dev/null || echo 0)" -gt "$(stat -c %Y "$live" 2>/dev/null || echo 0)" ]; then
    warn "store is newer than the live file — keeping the store (restore may be needed)"
    return 0
  fi
  _put "$live" "$STORE_FILE" && chmod 700 "$STORE_DIR"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "saved: $live -> $STORE_FILE ($(_json_cert_count "$STORE_FILE") certificates)"
  else
    warn "could not write the store at $STORE_FILE"
  fi
  return "$rc"
}

RESTORED=0  # set by cmd_restore when it actually copies the store back

cmd_restore() {
  local live
  live="$(resolve_live)" || { warn "cannot resolve the live storage (no traefik_certs volume, no override)"; return 2; }
  mkdir -p "$(dirname "$live")" 2>/dev/null || true
  if [ -s "$live" ] && _json_valid "$live"; then
    ok "live acme.json already has certificates ($(_json_cert_count "$live")) — nothing to restore"
    return 0
  fi
  if [ ! -s "$STORE_FILE" ] || ! _json_valid "$STORE_FILE"; then
    warn "nothing to restore: store has no valid acme.json yet ($STORE_FILE)"
    return 1
  fi
  if _put "$STORE_FILE" "$live"; then
    RESTORED=1
    ok "restored: $STORE_FILE -> $live ($(_json_cert_count "$STORE_FILE") certificates) — ACME will not re-request these"
    return 0
  fi
  warn "restore failed (could not write $live)"
  return 2
}

cmd_ensure() {
  cmd_restore
  local rc=$?
  if [ "$RESTORED" -eq 1 ] && [ -z "$LIVE_OVERRIDE" ]; then
    # restored into the volume — Traefik may have started with an empty store
    # (fresh volume after factory-reset); reload it so it serves what we have
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$TRAEFIK_CONTAINER"; then
      docker restart "$TRAEFIK_CONTAINER" >/dev/null 2>&1 \
        && ok "Traefik restarted to load the restored certificates" \
        || warn "could not restart $TRAEFIK_CONTAINER (it will pick the file up on its next start)"
    fi
  fi
  return "$rc"
}

cmd_status() {
  local live
  live="$(resolve_live 2>/dev/null || true)"
  echo "store: $STORE_FILE"
  if [ -s "$STORE_FILE" ] && _json_valid "$STORE_FILE"; then
    echo "       valid, $(_json_cert_count "$STORE_FILE") certificates, updated $(stat -c %y "$STORE_FILE" 2>/dev/null | cut -d. -f1)"
  else
    echo "       EMPTY/invalid"
  fi
  echo "live:  ${live:-<no traefik_certs volume>}"
  if [ -n "$live" ] && [ -s "$live" ] && _json_valid "$live"; then
    echo "       valid, $(_json_cert_count "$live") certificates"
  else
    echo "       EMPTY/invalid/absent"
  fi
}

case "${1:-status}" in
  save)    cmd_save ;;
  restore) cmd_restore ;;
  ensure)  cmd_ensure ;;
  status)  cmd_status ;;
  *)
    echo "usage: tls-store.sh {save|restore|ensure|status}" >&2
    exit 2
    ;;
esac
