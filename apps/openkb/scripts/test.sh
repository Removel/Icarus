#!/usr/bin/env bash
set -euo pipefail

app_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"

case "${1:-}" in
  "") tests=(tests/test_api.py tests/test_remove.py tests/test_report_ops.py tests/test_managed_kb_template.py tests/test_config.py tests/test_marketplace.py tests/test_skills.py tests/test_deck_prompt.py tests/test_deck_neon_prompt.py tests/test_bundled_skills.py tests/test_dev_scripts.py) ;;
  --full) tests=(tests) ;;
  *) echo "Usage: $0 [--full]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--full]" >&2
  exit 2
fi
if [ ! -x "$python_bin" ]; then
  echo "OpenKB development environment is missing. Run: icarus install openkb --dev" >&2
  exit 1
fi

cd "$app_dir"
"$python_bin" -m pytest "${tests[@]}" -q
"$python_bin" -m compileall -q openkb scripts tests
