# Icarus runtime images

Published to Docker Hub under `removel/icarus-*` for `linux/amd64` and
`linux/arm64`. The images contain no configuration or credentials; inject
everything at run time.

Images:

| Image | Internal port | Persistent data |
|---|---|---|
| `removel/icarus-webui` | 8080 | none |
| `removel/icarus-mem0` | 8000 | `/data` (history, FastEmbed cache) |
| `removel/icarus-openkb` | 7566 | `/data/config`, `/data/kbs`, `/data/backups` |
| `removel/icarus-rela-mem` | 8767 | none (PostgreSQL/FalkorDB are external) |

Gateway/agent and TUI are intentionally not published.

`docker run --env-file FILE` is the simplest injection method. Keep the env
files mode `600`; `docker inspect` still exposes environment variables to
anyone with Docker access, so use Docker/Kubernetes secrets where that matters.

## mem0

Container-side names (the `ICARUS_MEM0_*` names are control-plane aliases):

```dotenv
POSTGRES_HOST=100.70.8.11
POSTGRES_PORT=5432
POSTGRES_USER=removel
POSTGRES_PASSWORD=change-me
POSTGRES_DB=mem0_vectors
APP_DB_NAME=mem0_app
OPENAI_API_KEY=change-me
OPENAI_BASE_URL=https://api.deepseek.com
MEM0_DEFAULT_LLM_MODEL=deepseek-v4-flash
MEM0_DEFAULT_EMBEDDER_PROVIDER=fastembed
MEM0_DEFAULT_EMBEDDER_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
MEM0_DEFAULT_EMBEDDING_DIMS=384
JWT_SECRET=change-me
ADMIN_API_KEY=change-me
AUTH_DISABLED=false
MEM0_TELEMETRY=false
HISTORY_DB_PATH=/data/history/history.db
FASTEMBED_CACHE_PATH=/data/models
```

```bash
docker run -d --name icarus-mem0 --restart unless-stopped \
  --env-file /srv/icarus/mem0.env \
  -p 8888:8000 \
  -v /srv/icarus/mem0:/data \
  removel/icarus-mem0:latest
```

The external PostgreSQL must have the `vector` extension available.

## openkb

```dotenv
OPENKB_API_TOKEN=change-me
LLM_API_KEY=change-me
OPENAI_API_BASE=https://api.deepseek.com
OPENKB_ICARUS_MODEL=deepseek/deepseek-v4-flash
OPENKB_ICARUS_DEFAULT_KB=icarus-project
OPENKB_CONFIG_DIR=/data/config
OPENKB_KB_ROOT=/data/kbs
PAGEINDEX_API_KEY=
```

```bash
docker run -d --name icarus-openkb --restart unless-stopped \
  --env-file /srv/icarus/openkb.env \
  -p 7566:7566 \
  -v /srv/icarus/openkb:/data \
  removel/icarus-openkb:latest
```

## rela-mem

```dotenv
RELA_MEM_API_TOKEN=change-me
RELA_MEM_PG_HOST=100.70.8.11
RELA_MEM_PG_PORT=5432
RELA_MEM_PG_DATABASE=rela_mem
RELA_MEM_PG_USER=removel
RELA_MEM_PG_SCHEMA=rela_mem
RELA_MEM_PG_PASSWORD=change-me
RELA_MEM_FALKORDB_HOST=100.70.8.11
RELA_MEM_FALKORDB_PORT=6379
RELA_MEM_FALKORDB_GRAPH=rela_mem
RELA_MEM_FALKORDB_PASSWORD=change-me
```

```bash
docker run -d --name icarus-rela-mem --restart unless-stopped \
  --env-file /srv/icarus/rela-mem.env \
  -p 8767:8767 \
  removel/icarus-rela-mem:latest
```

## webui

```dotenv
ICARUS_WEBUI_HOST=0.0.0.0
ICARUS_WEBUI_PORT=8080
ICARUS_WEBUI_USER=change-me
ICARUS_WEBUI_PASSWORD=change-me
ICARUS_GATEWAY_ENDPOINT=http://gateway-host:8765
ICARUS_MEM0_ENDPOINT=http://mem0-host:8888
ICARUS_MEM0_API_KEY=change-me
ICARUS_OPENKB_ENDPOINT=http://openkb-host:7566
ICARUS_OPENKB_API_TOKEN=change-me
```

```bash
docker run -d --name icarus-webui --restart unless-stopped \
  --env-file /srv/icarus/webui.env \
  -p 8080:8080 \
  removel/icarus-webui:latest
```

`ICARUS_WEBUI_HOST=0.0.0.0` is non-loopback, so the user/password pair is
required or the process refuses to start. Endpoints must use addresses reachable
from the WebUI machine; `127.0.0.1` only works when the dependency is local to
that container.

## Build

```bash
scripts/publish-images.sh
# or: DOCKER_NAMESPACE=removel IMAGE_TAG=2026.10.19 scripts/publish-images.sh
```

The script uses the Docker Desktop default buildx builder, builds both platforms,
and pushes `latest` plus the requested tag. Set `BUILDX_BUILDER=name` to use a
dedicated builder instead.
