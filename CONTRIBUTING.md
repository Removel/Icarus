# Contributing to Icarus

## Scope and ownership

Icarus is an application-oriented Monorepo. Keep business code, dependency declarations,
lockfiles, Dockerfiles, app-owned Compose definitions, and development environments in the
owning `apps/<app>/`. The narrow exception is independently managed, repository-owned
middleware: its Compose definitions live in `infra/<component>/`, while `icarus.toml`
registers public topology, commands and environment bindings. Mem0's private PostgreSQL
remains bundled in its app-owned Compose project.

The repository root provides a Python 3.11+ stdlib-only lifecycle and validation control
plane, not a shared Python distribution, dependency manifest or environment. Applications
consume connection environment variables; they do not import the controller or read its TOML.

`bin/icarus` delegates to `scripts/icarus/`; Makefile targets are shortcuts to those
same entry points. AgentRuntime runs inside Gateway; do not introduce a separate Agent
process for convenience.

## Development and validation

Use each application's private environment. Agent and Gateway keep separate `.venv` directories;
both installers install the app-owned `apps/agent` editable distribution (`icarus` and `agent_app`).
Do not use repository-root PYTHONPATH as a replacement for installing Agent. Gateway/TUI retain their
own existing repository bootstrap for root-owned protocol/environment modules.

`make typecheck-agent` checks both canonical Python packages; `make check-package-agent` builds a wheel,
checks all 13 manifests and packaged defaults, and installs it into a disposable verification environment.
Gateway's suite includes a real TCP/WebSocket test with production runtime/plugins/SQLite and deterministic
local HTTP fixtures for external services; it is not a remote-model smoke.

Mem0 and OpenKB run in Docker; their
`.venv` directories are development/test environments, not additional production
services. Install a service test environment with `icarus install mem0 --dev` or
`icarus install openkb --dev`. This does not start a service or require model credentials.

Validate the smallest affected test first, then its application suite, then compile and
`git diff --check`. `make test-mem0` and `make test-openkb` run the offline contract gates;
`make test-mem0-full` and `make test-openkb-full` run the complete retained suites and
return their actual exit codes. Optional-provider collection errors are missing
prerequisites, not passing tests. Compare failures by test identity against the same
baseline and environment; do not fix unrelated failures or hide them with skip rules.

The root `make test` first runs `scripts/tests/` with the existing Agent pytest interpreter,
then aggregates application checks. No root test environment is installed. CI calls the same service scripts
rather than defining a second list of test files. Real-service/model smoke tests use
isolated data and require explicit execution/cost authorization. Never publish credentials
or use production databases as test fixtures.

## Dependencies and builds

OpenKB's private environment uses `uv sync --locked`; update and review `uv.lock` when
its dependency metadata changes. Its Dockerfile currently installs using pip and does
not consume that lock. Mem0 retains `poetry.lock` for provenance, but its current pip
installation does not consume it either. Do not label these Docker builds fully locked.
Other applications keep their existing app-owned requirements declarations.

Build application images from the repository's own Dockerfiles and app contexts. Do
not replace modified vendored source with an upstream distribution, publish images
without authorization, or centralize dependencies in a root requirements/venv.

## Spec-driven development

- Indivisible cross-application requirements: `spec/YYYY-MM-DD-<feature>.md`, containing
  common contracts, responsibilities, dependencies, and links to the owners.
- Application design and plans: `apps/<app>/docs/spec/YYYY-MM-DD-<feature>/`.
- Repository infrastructure design and plans: `docs/spec/YYYY-MM-DD-<feature>/`.

Feature directories contain `arch.md`, `plan.md`, and optional `plan-<purpose>.md`.
Use the first Git date and keep the name stable afterward. Do not create an empty
counterpart just to fill the directory. Templates are in `docs/templates/spec/`.
Architecture documents describe verified source/test behavior; distinguish proposed
changes from implemented behavior and update descriptions when code evolves.

## Skills

`.agents/skills/` contains development-time references and workflows. Root `skills/`
is reserved for Icarus production baseline skills; it does not automatically install
or inject them into the Agent. OpenKB's bundled deck themes/critic remain app-owned
runtime assets. Imported skills do not override this repository's approval, branch,
architecture, or data-protection rules.

## Branches and changes

`feature` is the integration branch. Application branches synchronize from it and merge
back into it. Split changes by logical feature or application layer, keep implementation
and tests together, and separate documentation-only changes when practical. Do not mix
unrelated refactors or experiment artifacts into a feature change.

Create branches, commit, amend, rebase, or push only when explicitly authorized. Run
relevant verification before an authorized commit. Never overwrite other developers'
working changes or stop unknown external services.

## Third-party source

Mem0 and OpenKB are maintained Icarus forks frozen at the recorded upstream import
commits, not untouched copies. Retain LICENSE/copyright notices and document changes in
the owning `MODIFICATIONS.md`; root `THIRD_PARTY_NOTICES.md` indexes source and skill
locations. Freeze means no automatic whole-tree upstream sync, not a ban on reviewed
security fixes. Do not inherit upstream CLA, account-vouching, release credentials,
or trademark endorsement claims.
