#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

"$repo_root/apps/agent/scripts/test.sh"
"$repo_root/apps/gateway/scripts/test.sh"
"$repo_root/apps/tui/scripts/test.sh"

cd "$repo_root"
git diff --check
