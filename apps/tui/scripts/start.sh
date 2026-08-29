#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/tui/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "TUI environment is missing. Run: make install" >&2
  exit 1
fi

export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m apps.tui.src.main "$@"
