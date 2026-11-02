#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
  printf '%s\n' 'Argus environment missing. Run: bin/icarus install argus --dev' >&2
  exit 1
fi
exec "$python_bin" -m argus_app.main "$@"
