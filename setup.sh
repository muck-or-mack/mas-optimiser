#!/usr/bin/env bash
# Reproducible install for mas-optimiser. Run from the repository root.
# Installs uv if the machine has none, builds the environment from uv.lock
# and runs the unit tests as a smoke test.

set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if [ ! -f pyproject.toml ] || ! grep -q '^name = "mas-optimiser"' pyproject.toml; then
  echo "ERROR: run this script from the mas-optimiser repository root." >&2
  exit 1
fi

# A machine without uv gets the version the tests run on
# (.github/workflows/ci.yml), from uv's own installer, in the user's own
# folder. A uv that is already installed is used as it is.
UV_VERSION=0.13.1
UV_BIN="$HOME/.local/bin"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found: installing uv $UV_VERSION into $UV_BIN (https://docs.astral.sh/uv/)."
  installer="https://astral.sh/uv/$UV_VERSION/install.sh"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf "$installer" | env UV_INSTALL_DIR="$UV_BIN" sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$installer" | env UV_INSTALL_DIR="$UV_BIN" sh
  else
    echo "ERROR: installing uv needs curl or wget, and neither was found." >&2
    exit 1
  fi
  # The installer puts the folder on PATH for new terminals only.
  export PATH="$UV_BIN:$PATH"
fi
echo "Using $(uv --version)."

# The benchmark and the loop code are submodules; a plain `git clone` leaves
# their folders empty.
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
