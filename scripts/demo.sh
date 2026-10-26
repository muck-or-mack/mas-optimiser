#!/usr/bin/env bash
# Demo: start the domain servers if needed, then run the `demo` experiment
# (configs/experiments/demo.yaml: two ITSM tasks with reasoning summaries).
#
#   ./scripts/demo.sh             # the demo experiment as configured
#   ./scripts/demo.sh --limit 1   # any option overrides the experiment file
#
# Needs Docker running and ANTHROPIC_API_KEY in .env. With Langfuse set up
# (scripts/langfuse.sh up) the run is traced as well.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not running; start Docker Desktop and try again." >&2
  exit 1
fi

# --wait returns once the servers' health checks pass, up to about 30 s on a
# cold start.
docker compose -f docker/compose.yaml up -d --wait

uv run mas-optimiser run demo "$@"
