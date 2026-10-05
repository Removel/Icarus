# Icarus-managed Mem0 server

Runtime uses [docker-compose.yaml](docker-compose.yaml) and [dev.Dockerfile](dev.Dockerfile)
from the application-owned source. Start/stop/status through `icarus` from the repository
root, rather than the removed upstream Makefile, production image, or local env template.

The first cleanup keeps the REST API (`http://127.0.0.1:8888/docs`), PostgreSQL/pgvector,
and dashboard (`http://127.0.0.1:3000`). Auth, API-key management, rate limits, telemetry,
and Alembic database migrations remain backend capabilities, even when UI changes later.
Runtime config and credentials come from the root `.env`; secrets are never committed.
Data lives under `$ICARUS_DATA_DIR/services/mem0`, including postgres/history/models/backups.
Stopping containers does not remove it. Back up data before database upgrades or recovery.

## Operations

These scripts are not dashboard-only assets and remain available inside the API service:

```bash
# From the Icarus repository root, with the service already running:
# seed uses the API port inside the container, not host-mapped :8888.
bash apps/mem0/scripts/icarus-compose.sh exec -e API_URL=http://localhost:8000 mem0 bash scripts/seed.sh

# Password reset requires EMAIL and PASSWORD environment values; no --help parser.
# Prune requires REQUEST_LOG_RETENTION_DAYS (default 30); running it deletes old logs.
# The Python scripts use the server's bare imports, so set PYTHONPATH=/app.
bash apps/mem0/scripts/icarus-compose.sh exec -e PYTHONPATH=/app -e EMAIL -e PASSWORD mem0 python scripts/reset_admin_password.py
bash apps/mem0/scripts/icarus-compose.sh exec -e PYTHONPATH=/app -e REQUEST_LOG_RETENTION_DAYS mem0 python scripts/prune_request_logs.py
```

Inspect each script's options and required environment before applying it. Seed initializes
API data, password reset changes administrative access, and prune deletes old request logs;
they are maintenance operations, not smoke tests. Obtain explicit authorization and use
backups where appropriate. Do not run them merely to validate repository cleanup.

The server imports the modified SDK installed from local `mem0/` source. Development
SDK tests use the private app `.venv`; production serving still uses Docker. See the
[application README](../README.md) and [modification history](../MODIFICATIONS.md).
