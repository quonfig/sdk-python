#!/usr/bin/env bash
#
# Fail when the integration-test-data refs pinned across this repo's workflows
# differ (qfg-6vno.7). Every `repository: quonfig/integration-test-data`
# checkout must carry a `ref:`, and all of them must be the same tag, so the
# chaos and failover gates can never lag the unit/contract pin again.
#
# Usage: scripts/check-itd-pins.sh [workflow-dir]   (default .github/workflows)

set -euo pipefail

dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.github/workflows}"

# One "file<TAB>ref" line per integration-test-data checkout step. A step runs
# from its `- name:`/`- uses:` item to the next one; `ref:` may come before or
# after `repository:` inside its `with:` block.
pins="$(
  for f in "$dir"/*.yml "$dir"/*.yaml; do
    [[ -f "$f" ]] || continue
    awk -v file="$(basename "$f")" '
      function flush() {
        if (itd) { print file "\t" (ref == "" ? "<missing>" : ref) }
        itd = 0; ref = ""
      }
      /^[[:space:]]*- (name|uses):/ { flush() }
      /^[[:space:]]*repository:[[:space:]]*quonfig\/integration-test-data[[:space:]]*$/ { itd = 1 }
      /^[[:space:]]*ref:[[:space:]]*/ { r = $0; sub(/^[[:space:]]*ref:[[:space:]]*/, "", r); sub(/[[:space:]]*(#.*)?$/, "", r); ref = r }
      END { flush() }
    ' "$f"
  done
)"

if [[ -z "$pins" ]]; then
  echo "::error::no quonfig/integration-test-data checkouts found under $dir" >&2
  exit 1
fi

echo "integration-test-data pins:"
echo "$pins" | sed 's/^/  /'

distinct="$(echo "$pins" | cut -f2 | sort -u)"
if [[ "$(echo "$distinct" | wc -l | tr -d ' ')" != "1" || "$distinct" == "<missing>" ]]; then
  echo "::error::integration-test-data refs differ across workflows ($(echo "$distinct" | tr '\n' ' ')). Bump them together." >&2
  exit 1
fi
echo "OK: every workflow pins integration-test-data at $distinct"
