# FalkorDB

Managed independently by `icarus start falkordb`; see [shared middleware](../README.md).
Authentication is required and browser access disabled. The shared network alias is
`falkordb`; the default loopback host port is `16379`. Durable append-only data lives
in `$ICARUS_DATA_DIR/services/falkordb`, independent of an application name.

`icarus install falkordb` pulls the pinned image without starting or deleting data.
