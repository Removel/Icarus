#!/usr/bin/env bash
set -euo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$repo_root/apps/argus/.venv/bin/python" -m apps.argus.src.migrate "$@"
