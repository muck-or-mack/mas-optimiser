#!/usr/bin/env bash
# Docs: build the documentation and open it in the browser.
#
#   ./scripts/docs.sh                               # the front page
#   ./scripts/docs.sh results                       # one page, by its path under
#   ./scripts/docs.sh methodology/acceptance-gate   # docs/, without the extension
#
# The published documentation, https://mas-optimiser.readthedocs.io/, is
# rebuilt from main on every push; this is for reading a change before it is
# pushed. The build goes to docs/_build/html and stops on any warning.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

uv run sphinx-build -W --keep-going -q docs docs/_build/html

page="docs/_build/html/${1:-index}.html"
if [ ! -f "$page" ]; then
  echo "ERROR: no such page: $page" >&2
  exit 1
fi

# Windows' browser from WSL, else the desktop's own opener.
if command -v wslpath >/dev/null 2>&1 && command -v explorer.exe >/dev/null 2>&1; then
  # explorer.exe returns 1 even when it has opened the page.
  explorer.exe "$(wslpath -w "$page")" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$page"
elif command -v open >/dev/null 2>&1; then
  open "$page"
else
  echo "Built. Open $page in a browser."
fi
