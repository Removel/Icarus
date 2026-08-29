#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mode=${1:-}

case "$mode" in
  ""|--dev) ;;
  *) echo "Usage: $0 [--dev]" >&2; exit 2 ;;
esac

for app in agent gateway tui; do
  if [ "$mode" = "--dev" ]; then
    "$repo_root/apps/$app/scripts/install.sh" --dev
  else
    "$repo_root/apps/$app/scripts/install.sh"
  fi
done

"$repo_root/scripts/install-commands.sh"
