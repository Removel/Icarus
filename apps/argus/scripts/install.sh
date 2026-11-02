#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH= cd -- "$app_dir/../.." && pwd)
python_bin=${PYTHON:-python3}
extras=''
case "${1:-}" in
  '') ;;
  --dev) extras='[test]' ;;
  *) printf 'Usage: %s [--dev]\n' "$0" >&2; exit 2 ;;
esac
if [ ! -x "$app_dir/.venv/bin/python" ]; then
  "$python_bin" -m venv "$app_dir/.venv"
fi
"$app_dir/.venv/bin/python" -m pip install -e "$repo_root/apps/agent" -e "${app_dir}${extras}"
