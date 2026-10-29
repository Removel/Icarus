# Local Environment Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the disposable Icarus environment with one WSL-local stack whose persistent data lives in `/home/removel_yi/.icarus`, with secret-only dotenv and no repository `.icarus-data` dependency.

**Architecture:** Repository TOML owns runtime metadata and dependency wiring. PostgreSQL/pgvector is one infrastructure service with separate least-privilege application databases; each application retains its own Compose and native process boundaries. Runtime environment variables are transport, not duplicate configuration sources.

**Tech Stack:** Python stdlib lifecycle CLI, TOML, Docker Desktop/Compose, PostgreSQL 17/pgvector, FalkorDB, pytest, existing app-owned virtual environments.

**Spec:** User-approved design in this conversation: fresh reset without backup/migration; WSL user data directory; `infra/postgres/init/db-init.sh`; targeted Docker cleanup; TOML topology and dotenv secrets.

## Global Constraints

- Data directory is `/home/removel_yi/.icarus` on this machine; portable default is `~/.icarus`.
- No backup/migration; delete only verified Icarus-owned resources and data.
- Preserve existing working-tree functionality changes, especially dynamic tool work. Do not branch, commit, rebase or push.
- Preserve external credentials; do not print secret values. Fresh internal credentials must match all consumers.
- Native Gateway/Argus/WebUI/TUI stay native. Keep application-owned Compose/build files in each app.
- Run smallest affected tests, affected suites, compile/diff checks, then actual deployment health and persistence checks.

## Review Focus

- Clean checkout without gitignored overlays must have the same shared-database topology.
- Passwords with reserved URL/SQL characters must work without leaking through diagnostics.
- PostgreSQL extension creation must occur in the consumer database before unprivileged use.
- Failed startups must be reported, not masked by configuration-validation success.
- Docker deletion must use inspected identities; unknown anonymous volumes are not automatically Icarus-owned.

### Task 1: Configuration ownership and regression tests

**Files:** `scripts/icarus/{registry,environment,main,compose_runtime,injection,configuration}.py`, `packages/runtime_environment.py`, `scripts/tests/`, `apps/argus/src/core/config.py`, `apps/argus/test/core/test_config.py`.

**Interfaces:** TOML runtime data root/nonsecret exports become child process environment; existing `Connection` exports remain host/container aware. Argus accepts split host/port/database/user plus secret password, retaining explicit DSN compatibility.

- [ ] Add failing tests for TOML runtime settings, data path normalization, secret-literal rejection and clean configuration isolation.
- [ ] Implement minimal stdlib configuration support and direct-entry environment loading without introducing domain dependencies into lifecycle infrastructure.
- [ ] Add split Argus DSN tests including reserved password characters, missing values and legacy compatibility; implement composition.
- [ ] Verify focused tests and preserve explicit process overrides required by existing CLI fixtures.

### Task 2: Canonical shared infrastructure and Compose definitions

**Files:** `icarus.toml`, `icarus.local.toml.example`, `.example.env`, `infra/postgres/{compose.yaml,init/db-init.sh}`, `infra/falkordb/compose.yaml`, app Compose files and affected infrastructure tests.

**Interfaces:** One `postgres` dependency serves `mem0_app`, `rela_mem`, `argus`; administrator secret is independent of application secrets. Compose uses canonical `icarus-*` project names and explicit development image tags, not development builds tagged as release `latest`.

- [ ] Test canonical dependency graph without local overlay and verify neutral database data paths.
- [ ] Replace previous init script with `db-init.sh`; provision restricted roles/databases, extensions and schema grants.
- [ ] Remove duplicate build tags and private Compose/network assumptions; export the declared network to Compose.
- [ ] Run static and real empty-cluster initialization tests, including application access and extension privileges.

### Task 3: Fresh machine configuration and scoped reset

**Files:** ignored `.env`, `icarus.local.toml`, app settings as needed, owned runtime resources.

**Interfaces:** External credentials retained; local secrets regenerated; all current nonsecret dotenv entries move to TOML/app-owned configuration. No `.icarus-data` references remain in active configuration.

- [ ] Inventory and inspect Docker labels/mounts, native processes, target directories and local nonsecret settings.
- [ ] Stop verified Icarus processes; remove inspected old Icarus containers, attached obsolete volumes and unused dedicated networks.
- [ ] Write secret-only dotenv and minimal local TOML; remove old init script and obsolete local configuration references.
- [ ] Reset authorized old data directories without backups; create fresh user data root with correct ownership.
- [ ] Build or reuse validated app image contents under canonical development tags; remove superseded Icarus tags/images only when safe.

### Task 4: Live acceptance and documentation

**Files:** infrastructure/app operation docs and this plan.

- [ ] Start databases and apps through the real lifecycle CLI; verify initialization, authentication, health, dependency addresses and data mounts.
- [ ] Perform bounded DB/API writes and restart persistence checks; avoid unsolicited external platform actions.
- [ ] Run affected suites, compile/diff checks; report unrelated failures and any unavailable external dependency explicitly.
- [ ] Verify Docker resources and active configuration no longer reference repository `.icarus-data`; delete it and confirm final inventory.
- [ ] Update operation documentation from actual implementation; report kept/deleted resources, data path and remaining limitations. No Git commit/push.
