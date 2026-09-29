#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin="$repo_root/apps/argus/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  echo "Argus environment is missing. Run: icarus install argus --dev" >&2
  exit 1
fi

cd "$repo_root"
"$python_bin" -m pytest apps/argus/test -q
"$python_bin" -m compileall -q apps/argus/src apps/argus/test packages
