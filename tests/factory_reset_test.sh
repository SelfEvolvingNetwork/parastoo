#!/usr/bin/env bash
# =============================================================
#  Factory reset — guard-rail tests (hermetic: no docker, no network, no root)
#
#  The 2026-11-04 incident: the old `parastoo reset` reported success
#  while the stack was actually broken (orphan containers + no health
#  gate). These tests lock in the hardening so it cannot regress:
#    1) the script is valid bash
#    2) it refuses to run without root (with a stubbed non-root `id`)
#    3) it requires confirmation and NEVER reads stdin (curl|bash safe)
#    4) every hardening step from the incident is present
#    5) `parastoo reset` delegates to the same script (one code path)
# =============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$ROOT/scripts/factory-reset.sh"
mkdir -p "$ROOT/tests/stub"
printf '#!/bin/sh\necho 1000\n' > "$ROOT/tests/stub/id"
chmod +x "$ROOT/tests/stub/id"

pass=0; fail=0
okchk()   { echo "  ok: $1"; pass=$((pass+1)); }
failchk() { echo "  FAIL: $1"; fail=$((fail+1)); }

# 1) syntax
if bash -n "$S" 2>/dev/null; then okchk "bash -n scripts/factory-reset.sh"; else failchk "bash -n scripts/factory-reset.sh"; fi

# 2) refuses without root (id stubbed to non-root) — must exit 1 BEFORE touching docker
if env PATH="$ROOT/tests/stub:$PATH" bash "$S" --yes >/dev/null 2>&1; then
  failchk "refuses to run without root (stubbed id)"
else
  okchk "refuses to run without root (stubbed id)"
fi

# 3) no stdin reads for confirmation — the interactive gate must use /dev/tty
if grep -qF '/dev/tty' "$S" && ! grep -qE '^ *read -r -p' "$S"; then
  okchk "confirmation reads /dev/tty, never stdin (curl|bash safe)"
else
  failchk "confirmation reads /dev/tty, never stdin (curl|bash safe)"
fi

# 4) hardening steps that fixed the incident
grep -qF '^[0-9a-f]{12}_parastoo-' "$S" && okchk "removes stale recreate orphans" || failchk "removes stale recreate orphans"
grep -qF 'volumes are gone (verified)' "$S" && okchk "verifies the volume wipe" || failchk "verifies the volume wipe"
grep -qF 'com.docker.compose.project=parastoo' "$S" && okchk "sweeps containers by compose project label" || failchk "sweeps containers by compose project label"
grep -qF 'up -d --build' "$S" && okchk "rebuilds the stack (up -d --build)" || failchk "rebuilds the stack (up -d --build)"
grep -qF 'localhost:8000/healthz' "$S" && okchk "health gate on the core before declaring success" || failchk "health gate on the core before declaring success"
grep -qF 'PARASTOO_SETUP_TOKEN' "$S" && okchk "prints the setup token at the end" || failchk "prints the setup token at the end"
grep -qF 'pre-reset-db-' "$S" && okchk "best-effort pre-reset backup" || failchk "best-effort pre-reset backup"
grep -qF 'docker compose down -v --remove-orphans' "$S" && okchk "compose down -v --remove-orphans" || failchk "compose down -v --remove-orphans"

# 5) CLI delegation — one code path
if grep -qF 'factory-reset.sh' "$ROOT/bin/parastoo"; then okchk "bin/parastoo delegates reset to factory-reset.sh"; else failchk "bin/parastoo delegates reset to factory-reset.sh"; fi

echo "factory_reset_test: pass=$pass fail=$fail"
[ "$fail" -eq 0 ] && exit 0
exit 1
