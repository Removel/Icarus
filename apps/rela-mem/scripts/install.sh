#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON:-python3}
"$python_bin" -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install -e "$app_dir[dev]"
