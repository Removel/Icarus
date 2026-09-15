#!/usr/bin/env bash
set -euo pipefail

python /app/scripts/prepare_managed_kb.py
exec python -m openkb.api --host 0.0.0.0 --port 7566
