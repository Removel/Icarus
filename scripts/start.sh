#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
gateway_script="$repo_root/apps/gateway/scripts/start.sh"
tui_script="$repo_root/apps/tui/scripts/start.sh"
gateway_python="$repo_root/apps/gateway/.venv/bin/python"
gateway_pid=""

cleanup() {
  if [ -n "$gateway_pid" ] && kill -0 "$gateway_pid" 2>/dev/null; then
    kill "$gateway_pid" 2>/dev/null || true
    wait "$gateway_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$gateway_script" &
gateway_pid=$!

ready=0
for _ in {1..100}; do
  if ! kill -0 "$gateway_pid" 2>/dev/null; then
    wait "$gateway_pid"
    exit $?
  fi
  if "$gateway_python" -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=0.2).read()' >/dev/null 2>&1; then
    if kill -0 "$gateway_pid" 2>/dev/null; then
      ready=1
      break
    fi
  fi
  sleep 0.1
done

if [ "$ready" -ne 1 ]; then
  echo "Gateway did not become ready at http://127.0.0.1:8765" >&2
  exit 1
fi

"$tui_script" "$@"
