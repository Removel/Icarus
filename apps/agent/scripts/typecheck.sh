#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
exec "$python_bin" -m mypy --config-file "$app_dir/pyproject.toml" -p icarus -p agent_app "$@"
