#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON:-python3}
requirements="$app_dir/requirements.txt"
agent_requirements="$app_dir/../agent/requirements.txt"

if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Icarus requires Python 3.11 or newer" >&2
  exit 1
fi

case "${1:-}" in
  "") ;;
  --dev) requirements="$app_dir/requirements-dev.txt" ;;
  *) echo "Usage: $0 [--dev]" >&2; exit 2 ;;
esac

if [ ! -x "$app_dir/.venv/bin/python" ]; then
  "$python_bin" -m venv "$app_dir/.venv"
fi

"$app_dir/.venv/bin/python" -m pip install \
  -r "$agent_requirements" \
  -r "$requirements"
