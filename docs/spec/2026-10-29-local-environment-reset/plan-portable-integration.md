# Portable Deployment Integration Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Bring the generic deployment fixes from `feat/argus` into the current `feature` working tree without importing Argus or local Agent settings.

**Architecture:** The repository registry owns public runtime metadata and dependency wiring. Mem0 and rela-mem share one PostgreSQL/pgvector instance with independent databases and restricted accounts; FalkorDB remains independent and OpenKB retains file storage. Each application keeps its own build and Compose files.

**Tech Stack:** Existing stdlib lifecycle CLI, Python app environments, TOML, Compose, PostgreSQL 17/pgvector, pytest.

**Spec:** The user's approved scope in this conversation: extract generic operations fixes into `feature`; old local databases have already been cleared. This task does not perform another reset or start services.

## Global Constraints

- Work in the explicitly requested `feature` working tree; preserve the pre-existing blank-line edit in `icarus.toml`.
- No branches, commits, merges, rebases, pushes or PRs. The remote `origin/feature` baseline was fetched and equals HEAD (`885a431`).
- Do not import Argus components, source, credentials, tests or requirements. Do not change `apps/agent/settings.json` or WebSearch.
- Do not read or modify secret values in `.env`. Do not install dependencies or mutate running infrastructure.
- Portable data root: `~/.icarus`; middleware data paths: `services/postgres` and `services/falkordb`.
- PostgreSQL administrator: `icarus_admin`; Mem0 database/account: `mem0_app`/`mem0_local`; rela-mem database/account/schema: `rela_mem`.
- Remove only the active Argus-specific section from the ignored machine overlay by commenting it, retaining its values for a future switch back.
- Test in order: focused regression tests, affected suites, compile and diff checks. Explicitly report unavailable live Docker verification.

## Review Focus

- A clean checkout must wire Mem0 to PostgreSQL without an ignored overlay or manually supplied connection host.
- Local Compose uses network DNS; remote database overrides use their configured host/port and are not started locally.
- Reserved URL characters and IPv6 addresses must survive database connection construction.
- Empty-cluster initialization must provision both applications, install vector in each database and stop on SQL errors; no Argus secret is required.
- Alternative dotenv selection and explicit process data-root overrides must retain isolation and precedence.

### Task 1: Runtime metadata and direct-entry loading

**Files:** `scripts/icarus/{registry,configuration,main}.py`, `packages/runtime_environment.py`, `scripts/tests/test_runtime_configuration.py`, `apps/agent/test/agent_app/config/test_runtime_environment.py`.

**Interfaces:** `Registry.data_dir: str | None`, `Registry.env: dict[str, str | int | bool]`; `load_configuration`, `IcarusControl._runtime` and existing `load_icarus_environment` keep their public call signatures.

- [x] Port isolated runtime tests first; verify failures for unsupported `[runtime]` and absent direct-entry data loading.
- [x] Port the generic implementation; validate nonsecret environment values and preserve process overrides and explicit dotenv selection.
- [x] Verify focused runtime tests and registry/configuration tests.

### Task 2: Shared infrastructure and app-only deployment

**Files:** `icarus.toml`, `icarus.local.toml.example`, `.example.env`, `infra/{postgres,falkordb}/compose.yaml`, `infra/postgres/init/db-init.sh`, the three app Compose files, `scripts/icarus/compose_runtime.py`, `scripts/tests/{test_clean_environment,test_repository_infra}.py`, ignored `icarus.local.toml`.

**Interfaces:** Both app dependency edges target `postgres`; `build_child_environment` exports each app's own database/account and chooses shared-network DNS or remote host/port. PostgreSQL init consumes only administrator, Mem0 and rela-mem passwords.

- [x] Add clean-topology, injection, network and initializer regression tests; observe expected failures against the old topology.
- [x] Transfer generic Compose/network/image changes and implement two-database initialization, independent administrator and neutral data paths; exclude all Argus bindings.
- [x] Update existing infrastructure assertions for the intended new paths and image tags.
- [x] Comment the machine overlay's obsolete Argus section; validate both real and clean-checkout configuration.
- [x] Verify focused tests and the full control-plane suite.

### Task 3: Mem0 connection correctness

**Files:** `apps/mem0/server/db.py`, `apps/mem0/mem0/vector_stores/pgvector.py`, `apps/mem0/{requirements-dev.txt,scripts/test.sh}`, `apps/mem0/tests/test_database_connection.py`.

**Interfaces:** Keep `_build_database_url()` and `PGVector` signatures unchanged; encode URL components rather than concatenating credentials.

- [x] Port connection regression tests and observe malformed-password failures before implementation.
- [x] Port SQLAlchemy URL construction and pgvector component quoting, including IPv6 formatting.
- [x] Verify focused connection tests and the default Mem0 suite.

### Task 4: Documentation, final verification and review

**Files:** root `README.md`, `infra/README.md`, middleware READMEs, app deployment READMEs, this plan.

- [x] Update active operational documentation to the two-app shared topology, TOML-owned runtime data and credentials-only dotenv; remove obsolete bundled-Mem0 claims.
- [x] Run affected Agent/Gateway/Mem0/OpenKB/rela-mem suites where existing environments permit; report failures rather than fixing unrelated code.
- [x] Run compileall, shell syntax and diff checks; confirm Agent settings and WebSearch are untouched and no Argus bindings were introduced.
- [x] Have one fresh reviewer inspect the entire uncommitted change; fix important findings with focused regression tests.
- [x] Leave all changes uncommitted on `feature`; summarize verification and remaining deployment steps.
