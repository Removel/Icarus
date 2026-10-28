#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON:-python3}

if [ "$#" -ne 1 ] || [ "$1" != "--dev" ]; then
  echo "Usage: $0 --dev" >&2
  exit 2
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Icarus development requires Python 3.11 or newer" >&2
  exit 1
fi

if [ -L "$app_dir/.venv" ]; then
  echo "Mem0 requires an app-owned private virtual environment, not a symlink" >&2
  exit 1
fi
if [ ! -x "$app_dir/.venv/bin/python" ]; then
  "$python_bin" -m venv "$app_dir/.venv"
fi
if ! "$app_dir/.venv/bin/python" -I -c 'import os, sys; raise SystemExit(sys.prefix == sys.base_prefix or os.path.realpath(sys.prefix) != os.path.realpath(sys.argv[1]))' "$app_dir/.venv"; then
  echo "Mem0 requires an app-owned private virtual environment" >&2
  exit 1
fi
cd "$app_dir"
PIP_CONFIG_FILE=/dev/null "$app_dir/.venv/bin/python" -I -m pip --isolated install -r "$app_dir/requirements-dev.txt"
