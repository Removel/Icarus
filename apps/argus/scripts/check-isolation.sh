#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$app_dir/.venv/bin/python" -c 'import json; from argus_app.rule_sdk.isolation import IsolationLauncher; result = IsolationLauncher().probe(); print(json.dumps({"available": result.available, "reason": result.reason})); raise SystemExit(0 if result.available else 1)'
