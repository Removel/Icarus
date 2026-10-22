#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/gateway/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "Gateway environment is missing. Run: make install-dev" >&2
  exit 1
fi

cd "$repo_root"
if [ "$#" -gt 0 ]; then
  "$python_bin" -m pytest "$@"
else
  "$python_bin" -m pytest apps/gateway/test -q
fi
"$python_bin" -m compileall -q apps/gateway/src apps/gateway/test packages
