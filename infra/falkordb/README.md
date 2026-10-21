# FalkorDB

Managed independently by `icarus start falkordb`; configuration and safe stop
semantics are described in [shared middleware](../README.md). Authentication is
required, browser access disabled, and durable append-only data retains the
existing relationship-memory data path. The shared network alias is `falkordb`.
`icarus install falkordb` pulls the pinned image without production credentials.
