# SDD ledger — plan: docs/spec/2026-10-29-local-environment-reset/plan-portable-integration.md

## Scope and rulings

- Ruling: implement in the explicitly requested `feature` working tree without creating a branch or commits — the task is selective integration, not a Git merge — cost: the uncommitted patch must be committed separately when requested.
- Ruling: reuse only the generic file-level differences from `feat/argus`, not its commit boundaries — those commits also contain Argus-dependent topology and unrelated historical changes — cost: later full Argus integration may need conflict resolution.
- Ruling: leave the user's existing blank line in `icarus.toml` intact — preserve unrelated workspace changes — cost: one harmless whitespace difference remains.
- Ruling: comment the ignored overlay's Argus-only section instead of weakening registry validation or importing Argus — enables the current nine-component branch while retaining the local reference — cost: switching back to Argus requires uncommenting it if still needed.
- Ruling: explicitly export Mem0's application database name as `mem0_app` alongside the vector-store database binding — keep the two server database consumers aligned even if obsolete dotenv values exist — cost: changing the database name requires updating both declarations and initialization.
- Ruling: do not reset, start or stop infrastructure; the user reports the old databases already cleared — no second destructive action or deployment needed for code integration — cost: live empty-cluster permissions and service readiness are not validated by this task.

## Execution

- Task 1: complete. Runtime regression observed 4 failed / 6 passed before implementation, then runtime/registry/direct-entry checks passed 179 tests.
- Task 2: complete. Shared-topology regression observed 9 failed / 1 passed before implementation. Both apps now receive `postgres:5432` with separate database/account bindings; remote targets use host addresses. Initializer runs with synthetic credentials and a controlled psql boundary, checks two application databases, vector/schema SQL and fail-fast. Control suite: 385 passed. Real machine `config validate`: 9 components.
- Task 3: complete. Connection regressions observed 4 failures before implementation. Reserved-password and IPv6 cases passed 4 tests after transfer; default Mem0 suite: 90 passed.
- Task 4: implementation and verification complete. Fresh review found Alembic ConfigParser percent interpolation, default-config reloading inside managed children and invalid direct-Compose maintenance instructions. Alembic and managed-entry regressions were observed failing before fixes; focused tests now pass (11 runtime/direct-entry and 5 Mem0 connection/migration tests). Maintenance documentation now selects exactly one running container using project/service/repository labels and uses `docker exec`, without reconstructing Compose environment.
- Final: fixed percent-encoded URLs at Alembic ConfigParser boundary — migration configuration regression RED→GREEN; raw URL is recovered unchanged.
- Final: fixed managed-child custom-config isolation — loader regression with intentionally invalid default TOML RED→GREEN; control plane sets `ICARUS_ENVIRONMENT_READY=1` and shared loader skips duplicate default TOML/dotenv loading.
- Final: fixed maintenance documentation — no credential-only dotenv interpolation or old `mem0-dev` project lookup is required; destructive maintenance commands were not run.
- Root/infra/app operational docs and lifecycle architecture updated from inspected implementation. All five Compose files passed `docker compose --env-file /dev/null ... config --quiet` using synthetic values. Shell syntax, compileall, diff checks passed. No Agent settings or WebSearch changes.

## Application verification

- Initial `make test-agent`: 1196 passed, 2 failed due to local dotenv values reaching passive wrapper tests: `knowledge/test_service_config.py::test_openkb_compose停止不依赖agent环境或完整运行配置` and `memory/test_service_config.py::test_mem0_compose停止不依赖agent环境或完整运行配置`. Subsequently fixed at the user's request with scoped environment isolation and explicit inherited-key/placeholder cases; ordinary full suite now passes 1201 tests.
- `PYTHON_DOTENV_DISABLED=1 make test-agent`: final review-fix run 1199 passed.
- Final combined control suite plus Agent runtime configuration tests: 390 passed.
- Final `make test-mem0`: 91 passed, including Alembic interpolation regression.
- `make test-gateway`: 50 passed, 1 dependency deprecation warning.
- `make test-openkb`: 335 passed, 2 unawaited-coroutine warnings.
- `bash apps/rela-mem/scripts/test.sh`: 41 passed, 29 integration tests deselected, 1 dependency deprecation warning.
- `bash apps/mem0/scripts/test.sh`: 90 passed.
- Final focused runtime/topology/direct-entry checks: 20 passed.
- Docker became available during this task; only version/image listing and Compose schema validation were used. No containers or database directories were mutated.

## Consistency follow-up and commit authorization

- User requested the remaining consistency/documentation fixes and authorized commits without co-author attribution; no push was requested.
- Logging Make targets now delegate to `icarus logs`; app wrappers use the canonical `icarus-mem0`/`icarus-openkb` project names. Mem0 creates only its mounted history/models directories, not a private PostgreSQL directory.
- New entrypoint/project/directory regression tests observed 8 failures before implementation and passed after correction. Contributor/WebUI documentation now distinguishes managed TOML configuration from manual dotenv entry points.
- Final verification: ordinary Agent suite 1201 passed; control suite 388 passed; Mem0 91 passed; OpenKB 335 passed (2 existing coroutine warnings); configuration valid with 9 components; diff checks passed.
- Commit scope excludes ignored machine files, `.claude/`, Agent settings and WebSearch. The pre-existing topology blank-line edit is preserved in the working tree and excluded from the staged topology patch.
