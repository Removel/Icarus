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
if ! command -v uv >/dev/null 2>&1; then
  echo "OpenKB development requires uv; install uv before retrying" >&2
  exit 1
fi

UV_PROJECT_ENVIRONMENT="$app_dir/.venv" uv sync --locked --project "$app_dir" --extra dev --extra api --python "$python_bin"
