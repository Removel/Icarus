# PostgreSQL

`icarus start postgres` manages the shared `pgvector/pgvector:pg17` service. The
loopback port is declared in TOML (default `15432`), with neutral persistent path
`$ICARUS_DATA_DIR/services/postgres`.

`init/db-init.sh` initializes an empty cluster with independent application accounts
and databases for Mem0, rela-mem and Argus. It installs vector before unprivileged
application use. Administrator credentials are separate from application credentials.
See [shared middleware](../README.md) for the database/credential table.

Initialization happens once. Changing TOML or dotenv does not migrate existing data,
create additional roles in an already initialized cluster or rotate its passwords.
`icarus install postgres` pulls the image without starting or resetting the service.
