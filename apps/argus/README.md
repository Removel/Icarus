# Argus

Argus is a plugin-hosted observation and rule application. It uses the existing
public `icarus` package rather than copying its runtime. The source is organized as:

```text
argus_app/
├── main.py                  # process entry
├── application.py           # FastAPI + database + Icarus Host lifecycle
├── config.py
├── infrastructure/          # exactly two PostgreSQL tables and transactions
├── contracts/               # facts, business events and directed capabilities
├── model_provider/          # typed semantic backend, separate from rule business
├── rule_sdk/                # constrained Python worker and immutable rule files
└── plugins/
    ├── protocols/           # QQ, Feishu, authenticated Agent callbacks
    ├── rule_capabilities/   # history, mechanical, semantic, memory/relationship/knowledge
    ├── workflow_rules/
    └── icarus_bridge/
```

## Install and start

Use the repository lifecycle:

```bash
bin/icarus install argus --dev
bin/icarus start argus
bin/icarus status argus
bin/icarus stop argus
```

Argus is opt-in, not added to the default stack. `main` starts the application;
`application` owns one database connection pool and one `PluginRuntimeHost`, starts
plugins before accepting business ingress, and stops its resources on shutdown.
`plugins/protocols` and `plugins/rule_capabilities` are namespace groups, not parent
plugins. Trusted builtin discovery traverses namespaces and stops at manifest leaves.

### Shared PostgreSQL

Production uses the existing `infra/postgres` container, a separate `argus` database,
and a restricted `argus` role. **There is no Argus PostgreSQL container.** Environment
injection uses `ARGUS_PG_HOST`, `ARGUS_PG_PORT`, `ARGUS_PG_DATABASE`, `ARGUS_PG_USER`,
`ARGUS_PG_PASSWORD`. A `postgresql+asyncpg://` `ARGUS_DATABASE_URL` may be supplied
for explicit deployments. Credentials must remain in ignored environment settings.

For a newly initialized shared cluster, its existing init script optionally creates
Argus's role/database if `ARGUS_PG_PASSWORD` is set. Existing clusters are **not**
changed by application startup: an administrator must provision the dedicated role
and database separately. Starting Argus never deletes/migrates existing data or stops
the shared database. App initialization creates only `observed_items` and `channels`.

### Required environment

- `ICARUS_DATA_DIR` or `ARGUS_DATA_DIR`: persistent rule directory.
- `ARGUS_AGENT_API_TOKEN`: authenticates Agent callbacks and trusted management.
- `ARGUS_GATEWAY_TOKEN`: authenticates Argus to Gateway.
- `ARGUS_GATEWAY_ENDPOINT`: Gateway HTTP address (existing WS endpoint is normalized).
- `ARGUS_PUBLIC_URL`: callback-reachable Argus HTTP base URL.
- `ARGUS_WORKSPACE_PATH`: dedicated Agent workspace, configured on Gateway process.
- `ARGUS_QQ_REPORT_SECRET`: signed OneBot ingress; absent means QQ endpoint disabled.
- `ARGUS_FEISHU_VERIFICATION_TOKEN`: official Feishu envelope/challenge verification.
- `ARGUS_FEISHU_ENCRYPT_KEY`: optional request signature secret; encrypted envelopes
  are currently rejected explicitly, not silently interpreted.

Optional semantic configuration uses existing typed Jev calls:
`ARGUS_JEV_API_KEY`, `ARGUS_JEV_BASE_URL`, `ARGUS_JEV_MODEL`.
Optional evidence uses `ICARUS_MEM0_ENDPOINT`, `ICARUS_RELATIONSHIP_ENDPOINT`,
`ICARUS_OPENKB_ENDPOINT` and their existing service tokens. Memory additionally requires
trusted `ARGUS_MEMORY_USER_ID`, `ARGUS_MEMORY_AGENT_ID`, `ARGUS_MEMORY_WORKSPACE_KEY`;
knowledge requires `ARGUS_KNOWLEDGE_BASE`. Rules cannot override these scopes.
Missing optional evidence returns unavailable rather than empty fabricated results.

## Event and confirmation flow

1. A protocol plugin validates and normalizes the message, commits the event, then
   publishes an Icarus event. Canonical source keys include account/tenant scope.
2. The rule plugin matches Channel subscriptions, loads the immutable rule file,
   and executes in a constrained worker. Historical queries use a captured committed
   bound per source and never acknowledge those records as processed.
3. A passed decision is persisted in bounded `channels.runtime`; Bridge freezes a
   round and submits a stable wake ID to a protected Agent Session.
4. The Agent adapter exposes Argus-only tools through the existing lazy tool surface,
   injects guidance in User Prompt, and acknowledges delivery only from the real
   complete model view.
5. `argus_confirm` reports handled/no_action. All frozen items must be accepted by
   Argus before any progress/subscription/rule activation. The Agent may instead
   finish unchanged. Callbacks themselves re-enter protocol persistence and rules.

`channels.runtime` stores bounded current pending/round/retry state, not an execution
history. No association, wake, rule-version, or confirmation table exists. A pending
capacity limit causes backpressure without advancing the source cursor.

### Rules

Actual first implementation uses a deliberately narrow Python function interface:

```python
def decide(event, channel):
    history = capability("history", {"limit": 20})
    relevant = any("review" in row["content"]["text"]
                   for row in history["observations"])
    return {"passed": relevant, "reason": "review evidence", "defer": False}
```

Available function names are `history`, `mechanical`, `semantic`, `memory`,
`relationships`, `knowledge`. Calls are dispatched through the existing typed Icarus
Capability channel. The worker gets plain JSON facts and Channel business fields,
not internal runtime/bindings or database credentials. `rule.check_requested` lints,
compiles, tests and writes a candidate; an acknowledged update switches its hash pointer.

Rules run in a separate isolated Python interpreter with an AST allowlist, OS resource
limits, a wall deadline and bounded JSON IPC. This is a **single trusted operator's
restricted execution environment, not a hardened hostile multitenant sandbox**.
Arbitrary imports, private attributes, file/network APIs and arbitrary SQL are not
exposed. Total capability calls are capped at three, semantic calls at one.

## Verification

```bash
bash apps/argus/scripts/test.sh
make test-agent
make test-gateway
make typecheck-agent
apps/agent/.venv/bin/python -m pytest scripts/tests -q
git diff --check
```

The Argus script creates and removes its own disposable test PostgreSQL container if
`ARGUS_TEST_DATABASE_URL` is absent. This is test infrastructure, not production topology.
A supplied URL must point at a disposable `*_test` database; repository tests truncate
only that test database. TCP E2E tests create and drop their own uniquely named test DB.
No automated pytest test uses a production database, paid model, or sends a real platform message.
The separate opt-in `test/manual_real_model.py --authorize-real-model --output <new-dir>`
can run explicitly authorized capped paid-model trials with synthetic ingress only;
its records and partial outcomes are described in the validation report.

`test_closed_loop.py` runs real PostgreSQL, both applications over TCP, actual Icarus
Host/AgentRuntime/lazy tools, and a deterministic local model endpoint. It verifies
QQ-before-Feishu history, no early Agent wake, confirmation-before-update rejection,
rule check/publication, and next-event suppression under the published rule.

Design and execution records: [spec directory](docs/spec/2026-11-01-channel-observation/plan-execution.md).

## Explicit limitations

The initial scheduler is a single instance with serialized rule/control execution;
there is no distributed leader or high-throughput fairness guarantee. Incomplete
external-task recovery retains uncertainty; accepted transport is not business
confirmation. Rules do not have arbitrary platform send tools. Official Feishu
`im.message.receive_v1` is supported; encrypted envelopes and additional edit/delete
subscriptions require explicit adapters. New-Channel discovery now handles only
unsubscribed sources using the configured initial rule (ARGUS_DISCOVERY_RULE_PATH,
ARGUS_DISCOVERY_ENABLED). It atomically seeds the first event without a new table.
A management WebUI and automatic data/file retention GC are not implemented.
Confirmation and later publication remain separate: an interrupted post-confirmation
update does not automatically recover its intent; see the real-model validation report.
