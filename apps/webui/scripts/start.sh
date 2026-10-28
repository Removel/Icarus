#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
python_bin=${PYTHON:-python3}

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "WebUI lifecycle requires Python 3.11 or newer" >&2
  exit 1
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "WebUI lifecycle requires Python 3.11 or newer" >&2
  exit 1
fi

# Run the launcher as a child instead of exec'ing it so the tracked process
# keeps this wrapper's command line, which the repository control matches by
# marker. icarus_start.py still execs Node in place of Python.
"$python_bin" "$repo_root/apps/webui/scripts/icarus_start.py" "$@"
