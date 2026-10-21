#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    printf '%s\n' 'Missing rela-mem environment: run bash apps/rela-mem/scripts/install.sh' >&2
    exit 1
fi
cd "$app_dir"
case "${1:-}" in
    "") "$python_bin" -m pytest test -m 'not integration' -q ;;
    --integration) "$python_bin" -m pytest test -q ;;
    *) printf '%s\n' 'Usage: test.sh [--integration]' >&2; exit 2 ;;
esac
"$python_bin" -m compileall -q src scripts test
