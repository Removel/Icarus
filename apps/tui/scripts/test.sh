#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/tui/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "TUI environment is missing. Run: make install-dev" >&2
  exit 1
fi

cd "$repo_root"
"$python_bin" -m pytest apps/tui/test -q
"$python_bin" -m compileall -q apps/tui/src apps/tui/test packages
