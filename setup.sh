#!/usr/bin/env bash
# Reproducible install for mas-optimiser. Run from the repository root.
# Builds the environment from uv.lock and runs the unit tests as a smoke test.

set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if [ ! -f pyproject.toml ] || ! grep -q '^name = "mas-optimiser"' pyproject.toml; then
  echo "ERROR: run this script from the mas-optimiser repository root." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi
echo "Using $(uv --version)."

# The benchmark is a submodule; a plain `git clone` leaves its folder empty.
git submodule update --init

# --locked stops with an error when uv.lock no longer matches pyproject.toml,
# instead of quietly resolving newer versions, so every machine installs the
# same pins. uv also fetches a matching Python if the system one is too old.
uv sync --locked

# The servers' seed databases ship zipped inside the benchmark repo. They are
# unpacked where its task configs expect them; its own .gitignore covers them.
GYM=vendor/enterpriseops-gym
if [ ! -d "$GYM/Domain Wise DBs and Task-DB Mappings" ]; then
  echo "Unpacking seed databases..."
  uv run python -m zipfile -e "$GYM/gym_dbs.zip" "$GYM"
fi

uv run pytest -q
echo "Install OK."
