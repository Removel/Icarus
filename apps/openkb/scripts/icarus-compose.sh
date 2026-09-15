#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin=${PYTHON:-python3}

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "OpenKB lifecycle requires Python 3.11 or newer" >&2
  exit 2
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "OpenKB lifecycle requires Python 3.11 or newer" >&2
  exit 2
fi

exec "$python_bin" "$repo_root/apps/openkb/scripts/icarus_compose.py" "$@"
