#!/usr/bin/env bash
#
# Run the failover + canonical-ordering chaos rigs against sdk-python (qfg-7h5d.1.8).
#
# Unlike run-chaos.sh (single upstream), these two rigs spawn their own
# api-delivery fixture upstream(s) from inside the pytest runner
# (chaos/test_failover_chaos.py):
#   - test_failover drives scenarios-failover/ against ONE upstream behind the
#     primary ('http') + 'secondary' proxies; faults hit the primary leg.
#   - test_ordering drives scenarios-ordering/ against TWO upstreams pinned to
#     divergent Meta.generations (one per scenario).
#
# So this wrapper only needs to boot toxiproxy; the test repoints the seeded
# 'http'/'secondary'/'sse' proxies at the upstreams it spawns. Mirrors
# sdk-go/scripts/run-failover-chaos.sh.
#
# Env knobs:
#   CHAOS_ONLY   comma list of scenario numbers to run,  e.g. "f02,o02"
#   CHAOS_SKIP   comma list of scenario numbers to skip, e.g. "o01"
#                (default: o01 — needs cross-leg max-wins, qfg-7h5d.1.14)
#   PYTEST_ARGS  extra args forwarded to pytest
#
# Examples:
#   ./scripts/run-failover-chaos.sh
#   CHAOS_ONLY=f02 ./scripts/run-failover-chaos.sh   # just the hang scenario
#   CHAOS_SKIP=    ./scripts/run-failover-chaos.sh   # include o01 (will be RED)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$SDK_DIR/.." && pwd)"
HARNESS_DIR="$REPO_ROOT/integration-test-data/chaos"

if [[ ! -d "$HARNESS_DIR" ]]; then
  echo "chaos harness not found at $HARNESS_DIR — is integration-test-data checked out as a sibling?" >&2
  exit 1
fi

# Identify ourselves to the shared chaos lock (qfg-47c2.32). Owner PID is THIS
# wrapper's pid so the lock survives the whole run, not just the short-lived
# start-chaos.sh subprocess.
export QUONFIG_CHAOS_SESSION="${QUONFIG_CHAOS_SESSION:-sdk-python-failover-$$-$(date +%s)}"
export QUONFIG_CHAOS_OWNER_PID=$$

# o01-secondary-newer needs cross-leg max-wins (qfg-7h5d.1.14); not implemented
# yet. Skip it by default (mirrors the sdk-go failover-chaos CI gate). Clear
# CHAOS_SKIP to include it (it will be RED).
export CHAOS_SKIP="${CHAOS_SKIP-o01}"

cleanup_done=0
cleanup() {
  if [[ "$cleanup_done" == "1" ]]; then
    return
  fi
  cleanup_done=1
  echo "==> tearing down chaos harness"
  "$HARNESS_DIR/stop-chaos.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> booting toxiproxy via shared launcher (no upstream — the test spawns its own)"
"$HARNESS_DIR/start-chaos.sh"

echo "==> running failover + ordering scenarios (CHAOS_ONLY=${CHAOS_ONLY:-<all>} CHAOS_SKIP=${CHAOS_SKIP:-<none>})"
cd "$SDK_DIR"
mise exec -- poetry run pytest -v -s chaos/test_failover_chaos.py ${PYTEST_ARGS:-}
