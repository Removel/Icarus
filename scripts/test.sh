#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Root control runtime is stdlib-only; pytest is supplied by the existing app env.
# Discover all control tests, including subprocess CLI fixtures, before app suites.
"$repo_root/apps/agent/.venv/bin/python" -m pytest "$repo_root/scripts/tests" -q
bash "$repo_root/apps/mem0/scripts/test.sh"
bash "$repo_root/apps/openkb/scripts/test.sh"
"$repo_root/apps/agent/scripts/test.sh"
"$repo_root/apps/gateway/scripts/test.sh"
"$repo_root/apps/tui/scripts/test.sh"

cd "$repo_root"
git diff --check
