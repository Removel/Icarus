# Shared middleware

The repository owns PostgreSQL/pgvector and FalkorDB here. App source and app-only
Compose files stay under `apps/`. `icarus.toml` declares the complete local dependency
graph: Mem0, rela-mem and Argus use one PostgreSQL instance, with separate databases
and application accounts. There are no generated Compose files under a data directory.

## Configuration

- `icarus.toml`: public runtime metadata, ports, dependencies and nonsecret exports.
- Ignored `icarus.local.toml`: machine-specific overrides; tables merge recursively.
- Ignored `.env`: credentials only. Copy `.example.env` and fill required secrets.
- `[runtime].data_dir` defaults to `~/.icarus`; the current WSL installation uses
  `/home/removel_yi/.icarus`. Explicit process `ICARUS_DATA_DIR` remains available for
  isolated tests/one-off invocations. The control plane exports the resolved path to apps.
- `[runtime.env]` carries nonsecret process-wide settings, such as proxy exclusions.
- App business settings remain in their owning application's settings file.

```bash
./bin/icarus config validate
./bin/icarus config show postgres
./bin/icarus install rela-mem
./bin/icarus start rela-mem
./bin/icarus status
./bin/icarus logs postgres --no-follow
./bin/icarus stop rela-mem
```

Configuration diagnostics do not start Docker or read secret values. `--config PATH`
selects another base and sibling `<stem>.local.toml`; operational relative paths remain
repository-relative. `ICARUS_ENV_FILE` selects a replacement dotenv for the control plane.

## Databases and permissions

The shared image is `pgvector/pgvector:pg17`; FalkorDB is `falkordb/falkordb:v4.22.1`.
Their loopback host ports are `15432` and `16379`. Image tags are not immutable digests.

| Database | Login role | Credential |
| --- | --- | --- |
| `mem0_app` | `mem0_local` | `ICARUS_MEM0_POSTGRES_PASSWORD` |
| `rela_mem` | `rela_mem` | `RELA_MEM_PG_PASSWORD` |
| `argus` | `argus` | `ARGUS_POSTGRES_PASSWORD` |

`icarus_admin` bootstraps the cluster using `ICARUS_POSTGRES_PASSWORD`. Application
roles are not superusers and cannot create roles/databases. `postgres/init/db-init.sh`
creates the databases, enables vector in Mem0/rela-mem databases and creates the rela-mem
schema. It runs only for an empty data directory. Editing secrets later does not rotate
existing database passwords; do that explicitly in PostgreSQL.

Root TCP probes do not certify authenticated SQL access; app readiness and acceptance
checks must validate credentials and schema access. External databases must be provisioned
beforehand; set `components.postgres.mode = "remote"` and reachable host/port in local TOML.
Never expose a private database by changing a loopback bind without reviewing security.

## Networks, images and data

The lifecycle creates the shared external `icarus` network. Container consumers get
`postgres:5432` / `falkordb:6379` only on the same declared shared network; native
consumers use host addresses. Compose's `ICARUS_NETWORK` is derived from that declaration.

Local app builds use `icarus-mem0:dev`, `icarus-openkb:dev`, `icarus-rela-mem:dev`.
They do not tag local builds as published `removel/icarus-*:latest`. Optional image choices
can be set in the component's TOML `env` via `ICARUS_MEM0_IMAGE`, `ICARUS_OPENKB_IMAGE`,
`ICARUS_RELA_MEM_IMAGE`, `ICARUS_POSTGRES_IMAGE`, `ICARUS_FALKORDB_IMAGE`.

Persistent database directories are `$ICARUS_DATA_DIR/services/postgres` and
`$ICARUS_DATA_DIR/services/falkordb`. Mem0 history/models and OpenKB knowledge/config
remain in their own `services/<app>` subdirectories. Stop/down does not delete data.
Do not mount one database directory from two containers concurrently. Older data paths
are not automatically migrated or removed.

Explicit stop affects the named runtime; `icarus stop` reverses the default required
closure and deduplicates owners. Remote/external resources are not taken over. Native
process identity and termination use Linux `/proc`.
