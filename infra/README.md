# Shared middleware

The repository owns independently managed middleware here; application source, builds and
app-only Compose definitions stay under `apps/`. The public registry is
[`icarus.toml`](../icarus.toml), not these Compose files. Mem0's private PostgreSQL remains
bundled in its app-owned three-container project. No Argus runtime is registered.

## Configure before starting

From the repository root:

```bash
cp -n .example.env .env
./bin/icarus config validate
./bin/icarus config show postgres
./bin/icarus config show rela-mem
```

These read-only diagnostics need neither Docker nor credentials and do not resolve Secret
values. There is no graphical configuration UI. With no machine overlay the default is
all-local; inspect any existing `icarus.local.toml` before starting. For remote deployments
only, copy `icarus.local.toml.example` to `icarus.local.toml` and edit its tunnel addresses
to endpoints reachable by the actual consumer.

- Base TOML → sibling local TOML → invocation-only CLI overrides; tables merge recursively.
- `--config PATH` selects another base and its sibling `<stem>.local.toml`, not the default
  machine overlay. Operational relative paths remain anchored to the repository root.
- Secrets and absolute `ICARUS_DATA_DIR` belong in ignored `.env` or process environment,
  never TOML. Process values win over dotenv; declared topology exports win over both,
  with variable-name-only conflict warnings.
- `ICARUS_ENV_FILE` selects a replacement dotenv for the control plane/Compose invocation.
  Standalone apps retain their own environment-loading behavior; it is not a new universal
  dotenv discovery rule for every app.
- `components.postgres.env` explicitly declares `ICARUS_POSTGRES_USER` and
  `ICARUS_POSTGRES_DATABASE`. These become the image's first-initialization `POSTGRES_USER`
  and `POSTGRES_DB`; no reverse scan of consumer edges occurs. Changing them or a password
  does **not** migrate an existing database, create new roles, or rotate credentials.

## Local or existing remote databases

```bash
./bin/icarus install rela-mem     # pull database images, build the app image; no service start
./bin/icarus start rela-mem       # required local databases first, then app readiness
./bin/icarus status
./bin/icarus logs postgres --no-follow
./bin/icarus stop rela-mem        # app only; shared databases stay running
```

The default PostgreSQL/FalkorDB host ports are `15432`/`16379`, bound to loopback. Images
are `pgvector/pgvector:pg17` and `falkordb/falkordb:v4.22.1`. PostgreSQL's tag fixes the major
version, not an immutable digest. Root probes check TCP reachability; authentication,
database/schema access and graph initialization are validated by the consuming app.

For existing databases, put topology overrides in ignored `icarus.local.toml`, for example:

```toml
[components.postgres]
mode = "remote"
host = "localhost"
port = 25432

[components.falkordb]
mode = "remote"
host = "localhost"
port = 26379
```

This example assumes an already established local tunnel and a native consumer. A Compose
consumer cannot use its own loopback to reach the host: supply a container-reachable address
instead. Provision remote PostgreSQL roles/database and permissions beforehand, and put
`RELA_MEM_PG_PASSWORD`, `RELA_MEM_FALKORDB_PASSWORD` and `RELA_MEM_API_TOKEN` only in the
selected environment. Remote install/stop are explicit no-ops; remote start only probes.
`reachable` does not certify authentication or authorize a write to a user's database.

An entire remote app can instead set `components.rela-mem.mode = "remote"` and an HTTPS
`endpoint`; its internal local databases and launch secrets are not required or managed.

## Network and retained data

On local shared-network starts the control plane creates the external `icarus` network if
needed. A local Compose consumer receives `postgres:5432` / `falkordb:6379` only when both
components declare the same shared network. Native consumers and remote dependencies use
registered host/port. Compose definitions interpolate `ICARUS_NETWORK`; an isolated stack
must set this and every participating component's `network` to the same unique name.
`RELA_MEM_IMAGE` can select a separate local app image tag.

**Before switching from the old combined rela-mem deployment, explicitly stop that old
Compose project. Never mount the same database directory in old and new containers at
once. There is no automatic active-mount collision guard or takeover.** The retained paths
are exactly:

- `$ICARUS_DATA_DIR/services/rela-mem/postgres`
- `$ICARUS_DATA_DIR/services/rela-mem/falkordb`

No migration, data deletion, network deletion or volume deletion is performed. Bare
`./bin/icarus stop` reverses the default stack's required closure and deduplicates owners;
explicit stop affects only the named runtime (bundled children resolve to their owner).
Remote resources and unrecognized external services remain untouched.

`install` uses explicit `image_action` (`pull` for these databases, `build` for app images).
Passive install/stop/logs interpolation placeholders never enter application startup.
Native managed-process identity and safe termination currently require Linux `/proc`; this
is not a cross-platform process manager. See the [current architecture](../docs/spec/2026-10-19-component-lifecycle/arch.md)
and [execution evidence](../docs/spec/2026-10-19-component-lifecycle/execution.md).
