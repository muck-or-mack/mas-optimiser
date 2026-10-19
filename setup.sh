#!/usr/bin/env bash
# Reproducible install for mas-optimiser. Run from the repository root.
# Builds the environment from uv.lock and runs the unit tests as a smoke test.

set -euo pipefail

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

# --locked stops with an error when uv.lock no longer matches pyproject.toml,
# instead of quietly resolving newer versions, so every machine installs the
# same pins. uv also fetches a matching Python if the system one is too old.
uv sync --locked

uv run pytest -q
echo "Install OK."
