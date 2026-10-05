#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"

case "${1:-}" in
  "") tests=(tests/memory/test_main.py tests/memory/test_storage.py tests/test_dev_scripts.py) ;;
  --full) tests=(tests) ;;
  *) echo "Usage: $0 [--full]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--full]" >&2
  exit 2
fi
if [ ! -x "$python_bin" ]; then
  echo "Mem0 development environment is missing. Run: icarus install mem0 --dev" >&2
  exit 1
fi

cd "$app_dir"
export MEM0_TELEMETRY=false
export PYTHONPATH="$app_dir/server${PYTHONPATH:+:$PYTHONPATH}"
"$python_bin" -m pytest "${tests[@]}" -q
"$python_bin" -m compileall -q mem0 scripts tests
