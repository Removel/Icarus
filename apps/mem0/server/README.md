# Icarus-managed Mem0 server

Runtime uses [docker-compose.yaml](docker-compose.yaml) and [dev.Dockerfile](dev.Dockerfile)
from the application-owned source. Start/stop/status through `icarus` from the repository
root, rather than the removed upstream Makefile, production image, or local env template.

The runtime is API-only (`http://127.0.0.1:8888/docs`). The dashboard and bundled
PostgreSQL were removed from the Compose file. The default registry connects Mem0 to
shared PostgreSQL as `mem0_local`, using the independent `mem0_app` database; the control
plane derives `ICARUS_MEM0_POSTGRES_*` from its declared dependency. Auth, API-key
management, rate limits, telemetry, and Alembic database migrations remain backend
capabilities. Topology and the data root come from repository TOML; credentials come
from the root `.env` and are never committed. History and models live under
`$ICARUS_DATA_DIR/services/mem0`; the shared cluster lives under `services/postgres`.
Stopping containers does not remove data. Back up data before database upgrades or recovery.

## Operations

These scripts remain available inside the API service:

```bash
# From the Icarus repository root, with the managed service already running:
# Resolve the running API by repository/project/service labels, without reloading Compose.
mem0_container=$(docker ps -q \
  --filter label=com.docker.compose.project=icarus-mem0 \
  --filter label=com.docker.compose.service=mem0 \
  --filter "label=com.docker.compose.project.config_files=$PWD/apps/mem0/server/docker-compose.yaml")
# Refuse an empty or ambiguous selection. Adjust the project label for a custom topology.
[ "$(printf '%s\n' "$mem0_container" | grep -c .)" -eq 1 ] || { printf '%s\n' 'Expected exactly one running Mem0 API'; exit 1; }

# Seed uses the API port inside the container, not the host-mapped port.
docker exec -e API_URL=http://localhost:8000 "$mem0_container" bash scripts/seed.sh

# Password reset requires exported EMAIL and PASSWORD; no --help parser.
# Prune requires exported REQUEST_LOG_RETENTION_DAYS (default 30) and deletes old logs.
# The Python scripts use the server's bare imports, so set PYTHONPATH=/app.
docker exec -e PYTHONPATH=/app -e EMAIL -e PASSWORD "$mem0_container" python scripts/reset_admin_password.py
docker exec -e PYTHONPATH=/app -e REQUEST_LOG_RETENTION_DAYS "$mem0_container" python scripts/prune_request_logs.py
```

Inspect each script's options and required environment before applying it. Seed initializes
API data, password reset changes administrative access, and prune deletes old request logs;
they are maintenance operations, not smoke tests. Obtain explicit authorization and use
backups where appropriate. Do not run them merely to validate repository cleanup.

The server imports the modified SDK installed from local `mem0/` source. Development
SDK tests use the private app `.venv`; production serving still uses Docker. See the
[application README](../README.md) and [modification history](../MODIFICATIONS.md).

The administrative memory paging endpoint and its snapshot/field boundaries are
documented in the [memory browser architecture](../docs/spec/2026-10-05-memory-browser/arch.md).
