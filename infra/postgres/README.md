# PostgreSQL

Managed independently by `icarus start postgres`; configuration and safe stop
semantics are described in [shared middleware](../README.md). The registry
exports the loopback bind port, bootstrap user/database and password reference.
The preserved relationship-memory PostgreSQL data path is not migrated or reset.
`icarus install postgres` pulls the configured image without production credentials.
Bootstrap user/database/password apply only to an empty data directory: changing environment
values does not migrate a database, create roles in an existing instance or rotate passwords.
The provider declares bootstrap in `components.postgres.env`, independently of consumer edges.
