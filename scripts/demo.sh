#!/usr/bin/env bash
# Demo: the benchmark's own ReAct agent on the first ITSM tasks with Claude
# Haiku 5.5. Starts the domain servers if needed, then runs the tasks.
#
#   ./scripts/demo.sh                      # first five tasks
#   ./scripts/demo.sh 2                    # first two
#   ./scripts/demo.sh 2 --thinking-summary # any further flags go to the runner
#
# Needs Docker running and ANTHROPIC_API_KEY in .env. With Langfuse set up
# (scripts/langfuse.sh up) the run is traced as well.

set -euo pipefail
cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker is not running; start Docker Desktop and try again." >&2
  exit 1
fi

# --wait returns once the servers' health checks pass, up to about 30 s on a
# cold start.
docker compose -f docker/compose.yaml up -d --wait

uv run python -m mas_optimiser.benchmarks.enterpriseops.runner \
  --model configs/models/haiku-5-5.yaml --domain itsm --limit "${1:-5}" "${@:2}"
