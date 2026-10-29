# SDD ledger — plan: docs/spec/2026-10-29-local-environment-reset/plan.md

## Scope and rulings

- User approved fresh Icarus-only reset without backups/migration, data root `/home/removel_yi/.icarus`, TOML topology and dotenv credentials, initialization filename `db-init.sh`.
- Ruling: use current dirty `feat/argus`, no Git/worktree mutations — this is a machine environment operation involving ignored config and mounts — cost: pre-existing uncommitted changes need careful separation.
- Ruling: retain anonymous volumes/build cache without verified Icarus ownership — avoid deleting other workloads — cost: unreclaimed storage remains. No global prune used.
- Ruling: local Mem0 endpoint is `http://127.0.0.1:18888`, not 8888 — Docker Desktop failed to publish 8888 even for a disposable HTTP container, while 18888 worked; all consumers inherit TOML — cost: local URL differs from repository default.
- Ruling: do not fold dynamic-tool fixture fixes into environment reset — user explicitly excluded functionality changes — cost: Agent/Argus suites remain partially red and must be reported.

## Execution

- Task 1 complete: root `[runtime]` metadata, user data-root expansion, direct app runtime loading, explicit dotenv isolation and Argus split DSN. Tests observed RED→GREEN. Process data-root overrides retained for isolated invocations.
- Task 2 complete: canonical graph works without local overlay; one PG instance and three independent restricted application roles/databases; independent administrator; `db-init.sh` creates per-database vector extensions/schema. Neutral middleware directories, five explicit runtime images, no local release-latest tagging. All five Compose schemas validated.
- Task 3 complete: user reconfirmed exact deletion list and changed permission mode after Auto-mode denials. Removed eleven inspected stopped containers plus attached anonymous test volume; removed six old dedicated networks. Cleared both authorized data roots, deleted repository `.icarus-data`, rebuilt `/home/removel_yi/.icarus` with mode 0700. Generated new local internal credentials, preserved provider/QQ credentials, wrote secret-only dotenv (19 keys). Removed three old app images and one inspected unreferenced old PG image. Final tagged images: `icarus-mem0:dev`, `icarus-openkb:dev`, `icarus-rela-mem:dev`, `pgvector/pgvector:pg17`, `falkordb/falkordb:v4.22.1`.
- Task 4 complete for environment: five Docker services healthy/reachable; native Gateway, Argus, WebUI running; TUI intentionally not started. Seven health/authenticated API checks passed including WebUI→Mem0 proxy. Three app SQL users each authenticated and wrote successfully; restart retained test row; all acceptance tables removed. Vector exists in mem0_app/rela_mem and application roles have no superuser/createdb rights. All data mounts verified under user root. Argus workspace moved to user root; MCP no longer depends on external launcher with hardcoded token.

## Independent review and corrections

- Reviewer found explicit envfile isolation regression: fixed with `test_runtime_respects_explicit_dotenv_isolation` RED→GREEN.
- Reviewer found reserved-character Mem0 passwords: server SQLAlchemy URL construction and pgvector URL encoding fixed; two focused tests RED→GREEN. Existing PostgreSQL prerequisites added only to app-owned dev requirements. Mem0 image rebuilt with fixes.
- Reviewer found missing-DSN test inherited split env: isolated host/password deletion; synthetic inherited environment reproducer RED→GREEN.

## Verification

- Root control suite: 382 passed.
- Mem0 default gate: 88 passed (includes two new connection tests).
- OpenKB default gate: 335 passed, two unawaited-coroutine warnings.
- Gateway suite: 164 passed, one dependency deprecation warning.
- Argus config: 15 passed; shared runtime/MCP focused: 15 passed.
- Argus full isolated-PG suite: 237 passed, 4 failed. Failed `test_real_model_reports_initial_through_gateway_and_argus` (both variants) and `test_semantic_handoff_keeps_unread_body_out_until_agent_reads_and_reuses_session` (normal/delayed-ack). Fixtures directly call nonresident Argus tools; output includes `Tool is not registered: argus_read`. Not hidden as an environment error.
- Agent full suite: 1387 passed, 11 failed. See `/tmp/icarus-reset-agent-suite.log`: background runtime/drain, config default budget assertions, Argus integration/notifications, service-wrapper inherited environment, automatic-recall deadline and plugin-count packaging assertions. No claim of all-green application suite.
- Final configuration validation, secret-only dotenv assertion, live authenticated HTTP checks, compileall and `git diff --check` passed.
- No commit or push. Existing unrelated working-tree changes preserved.
