#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
env_file="$repo_root/.env"
python_bin="$repo_root/apps/agent/.venv/bin/python"

if [ ! -f "$env_file" ]; then
  echo "Mem0 requires $env_file; copy .example.env first." >&2
  exit 2
fi
if [ ! -x "$python_bin" ]; then
  echo "Agent environment is missing. Run: make install-agent" >&2
  exit 2
fi

exec "$python_bin" "$repo_root/apps/mem0/scripts/icarus_compose.py" "$@"
