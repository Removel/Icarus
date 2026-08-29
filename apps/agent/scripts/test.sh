#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/agent/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "Agent environment is missing. Run: make install-dev" >&2
  exit 1
fi

cd "$repo_root"
"$python_bin" -m pytest apps/agent/test -q
"$python_bin" -m compileall -q apps/agent/src apps/agent/test packages
