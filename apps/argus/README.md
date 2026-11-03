# Argus

Argus is an opt-in Icarus application for observing messages, maintaining Channels,
and deciding when a normal Agent task should process new evidence. Concrete plugins
remain flat; `protocols/` and `rule_capabilities/` are discovery namespaces, not parent plugins.

## Install, prerequisites and start

```bash
bin/icarus install argus --dev
bash apps/argus/scripts/check-isolation.sh
bin/icarus start argus
bin/icarus status argus
bin/icarus stop argus
```

Rule execution requires Linux/WSL2 user/mount/pid/network namespaces (`unshare`),
`/usr/bin/python3` with the standard library, and `libseccomp.so.2`. No downloaded
launcher is executed or installed automatically. A failed isolation probe blocks rule
execution; ingress can still be stored and health exposes the execution failure.
There is no fallback to ordinary host Python execution.

Production uses the existing shared PostgreSQL instance with a separate `argus` DB
and restricted account, not a new production container. Supply `ARGUS_PG_HOST`,
`ARGUS_PG_PORT`, `ARGUS_PG_DATABASE`, `ARGUS_PG_USER`, `ARGUS_PG_PASSWORD`, or an explicit
`postgresql+asyncpg://` `ARGUS_DATABASE_URL`. Startup creates only `observed_items`
and `channels`; it does not migrate/delete production data or create database roles.

Other configuration:

- `ICARUS_DATA_DIR` or `ARGUS_DATA_DIR`: durable content-addressed rule files.
- `ARGUS_AGENT_API_TOKEN`, `ARGUS_GATEWAY_TOKEN`: callback and Gateway authentication.
- `ARGUS_GATEWAY_ENDPOINT`, `ARGUS_PUBLIC_URL`, `ARGUS_WORKSPACE_PATH`: service/workspace addresses.
- `ARGUS_QQ_REPORT_SECRET`, `ARGUS_FEISHU_VERIFICATION_TOKEN`: platform ingress verification.
- `ARGUS_FEISHU_ENCRYPT_KEY`: optional signature validation; encrypted envelopes are explicitly rejected.
- `ARGUS_JEV_API_KEY`, `ARGUS_JEV_BASE_URL`, `ARGUS_JEV_MODEL`: genuine typed semantic backend.
  The default base/model are `https://openrouter.ai/api` and `~typesafe/jev-latest`;
  OpenRouter requests use `/api/alpha/decisions`. The leading `~` is required.
  Explicit Argus overrides take precedence; self-hosted native JEV uses `/v1/systemone`.
  Missing key means semantic unavailable, including default semantic discovery. The key must
  be accepted by the selected route; key presence is not proof of working authentication.
- `ARGUS_SAFETY_REVIEW_ENABLED` (default false), `ARGUS_SAFETY_REVIEW_BUDGET` (default16):
  optional fail-closed typed LLM review before publishing candidate commit rules; static lint never invokes it.
- `ARGUS_DISCOVERY_ENABLED`, `ARGUS_DISCOVERY_RULE_PATH`: ongoing discovery and optional replacement script.
- `ARGUS_DISCOVERY_FALLBACK_ENABLED` (default false): enable a single tool-free LLM decision
  after a valid low-confidence semantic discovery result, not after transport/authentication errors.
- `ARGUS_DISCOVERY_FALLBACK_MODEL`: an existing **named model key** in Agent settings, not a wire model ID.
  `ARGUS_DISCOVERY_FALLBACK_SETTINGS_PATH` optionally selects that settings JSON; otherwise the
  existing `ICARUS_SETTINGS_PATH` / default settings lookup applies. Credentials and base URLs
  come from the existing general-model configuration, not from `ARGUS_JEV_API_KEY`.
- Existing Mem0/relationship/OpenKB endpoint/token configuration provides read-only evidence.
  Memory also requires `ARGUS_MEMORY_USER_ID`, `ARGUS_MEMORY_AGENT_ID`, `ARGUS_MEMORY_WORKSPACE_KEY`;
  knowledge requires `ARGUS_KNOWLEDGE_BASE`. Scripts cannot override trusted scopes.

Keep all credentials in ignored environment configuration, never rule source or prompts.

## Discovery confidence and fallback

Discovery uses one JEV `Choice` question and its native `confidence`, with a `0.7` gate.
The selected option's probability is a different field; no independent Noul question is
interpreted as route confidence. Ordinary semantic match keeps its `0.5` threshold.

The default rule defines a Channel as a context that needs an Agent response or processing:
it can be a greeting/short chat or a large multi-stage task. Group response decisions are
semantic, not mechanically gated on an @ mention. The rule supplies an explicit `criterion`
and bounded trusted conversation/history `context` to discovery; model adapters do not define
which business messages deserve a response. A new active Channel keeps an effective default
follow-up rule even if the first commit omits `rule_source`.

A host-observed low-confidence defer, or a rule's explicit `review_required=true` on a defer,
may invoke the perception fallback. Plain rule defer without that flag, candidate overflow,
invalid input and service errors do not. The fallback returns only
`DiscoveryDecision` (`new`/`existing`/`ignore`/`defer`), has no tools, and cannot create a Channel
or choose an identity outside the trusted candidates. Its SDK retries are zero, output cap
2048 tokens and call timeout20s; it runs outside rule execution and database/discovery locks.

The existing Observation stores a durable claim and result under `detail.discovery.fallback`.
A revision gets at most one upstream fallback attempt, including after restarts. A saved result
can be replayed through normal transactional checks; a claimed request with unknown outcome
is not automatically resent. The claim expires after25s and becomes unknown if unresolved.
This intentionally trades automatic recovery for a strict attempt budget after crashes.
Message revision and candidate versions are rechecked before applying an answer. Fallback
failure or ambiguity is not a successful classification and never silently becomes ignore.

## Decision files

A file has a synchronous `main(event, channel)` entry point; normal helper functions,
constants, safe computation imports, loops and exception handling are supported:

```python
import re

CRITERION = "Does this message itself announce approval, not denial, a quotation or hypothetical condition?"

def normalize(text):
    return re.sub(r"\s+", " ", text).strip()

def main(event, channel):
    answer = capability("semantic", {
        "text": normalize(event["content"].get("text", "")),
        "criterion": CRITERION,
        "context": {"goal": channel["goal"], "progress": channel["progress"]},
    })
    return {"passed": answer["match"], "reason": "Actual business conclusion"}
```

Return `passed: bool`, `reason: str`, optionally `defer: bool` and `evidence_refs: list[str]`;
passed/defer cannot both be true. Capability names are history/mechanical/semantic/memory/
relationships/knowledge. These compute or read evidence; they cannot write business state.
Errors remain errors, not ignored messages or fabricated empty evidence.

`argus_rule_check(source)` only parses, compiles and checks decision-only policy. It never
imports/executes candidate code, runs samples, calls a model, writes a candidate file or
activates a rule. Passing lint is not proof of business correctness. Top-level code is
limited to imports, literal computation constants, function declarations and docstrings.
Private reflection, dynamic evaluation/imports, unsafe modules and broker replacement are rejected.

Approved computation roots: re/json/math/datetime/statistics/itertools/functools/collections.
No direct host files, environment credentials, network, SQL, process creation or platform
writes. OS enforcement uses installed `unshare` namespaces, an empty read-only chroot,
cleared environment and inherited-FD cleanup, plus seccomp installed before any rule code.
The worker's stdout is separated from its protocol. Limits include 32KiB UTF-8 source,
4000 AST nodes, CPU2s, memory192MiB, wall default10s, output131000 bytes, three capabilities
and at most one semantic call. This is layered containment, not a claim of immunity to
kernel vulnerabilities or hostile multitenant certification.

## Atomic business commit

1. Protocol persists an observation before publishing its event.
2. Existing subscribed Channels evaluate first; multiple Channels may accept the same
   observation. Acceptance is persisted with the decision before notification and survives
   Agent commit, pending cleanup and subscription removal. New-Channel discovery is only a
   fallback after all existing subscribers have evaluated and rejected the observation;
   unread, deferred, failed or paused evaluations are not rejection. Ingress and background
   recovery use the same gate, and creation/fallback claims recheck it transactionally.
   A source subscription alone does not suppress a genuinely new topic. Discovery uses
   trusted candidate summaries, typed Choice/confidence and bounded retries. Recovery scans
   advance bounded position-based pages and wrap back, so unresolved early observations
   cannot monopolize the global batch or starve unrelated later requests.
3. Bridge freezes passed observations, sends a bounded current Channel snapshot and SDK
   metadata through the authenticated Gateway to the normal Agent execution chain.
4. Agent inherits the normal Session's authorized capabilities, including configured MCP servers.
   It directly performs actions (for QQ, SnowLuma MCP) and checks platform business results;
   normal assistant output is not a platform reply. It need not always read Channel first or
   search known tools again. Explicit ordinary session tool policies still apply.
5. Agent calls `argus_commit(results, progress?, source_keys?, rule_source?, lifecycle?, next_session?)`.
   Code supplies binding/ref mapping/request identity/version/hash/path. Results must cover
   exactly the frozen batch. Omitted fields stay unchanged.
6. Argus statically checks and prepares any new rule outside the transaction, then commits
   final results, Channel state, subscription/rule pointer, pending/round closure and receipt
   in one DB transaction. Only `applied=true` and `committed=true` means business success.

Old confirm/update/finish controls are no longer public operations. Failed commit leaves old
state and rule active together. Unchanged commits close the batch without incrementing the
business version. Immutable file writes are fsynced before activation; unused prepared files
may remain after a rejected DB commit.

Agent persists callback intent before HTTP. Retries reuse exact identities and bodies.
`GET /agent/commits/{binding_id}/{request_id}` reads only existing receipts and returns
not_found/pending/applied/rejected even after a round closes. Cancelled/stopped tasks only query
existing intents; unknown outcomes retain the round rather than replay business actions.
The transaction does not roll back external service actions. Terminal Agent execution without
an applied commit is not automatically replayed: the round remains visible as
`execution_outcome_unknown` unless the remote explicitly proves replay safe. Reconcile unknown
sends rather than starting another Session to repeat them.

Channel progress, rules and lifecycle remain the business representation; no extra JSON
`state` field or Argus table migration was introduced. Each round freezes a `session_key`;
`next_session="new"` changes the next execution context only after a successful commit.
The Agent owns durable 1:N Session links in its existing SessionStore. Deleted selection keys
stay consumed, and old callbacks always write the original Session journal.

QQ ingress verifies the HMAC over the raw body before dispatch. Normal heartbeat/notice/request
events are acknowledged without ingestion; message/message_sent are normalized with sender,
conversation kind, peer and self metadata. Self-sent private events need a reliable target_id.
Keeping self echoes from triggering loops is not deterministically guaranteed by semantic
inference alone; do not treat an unverified message_id summary as proof of authorship.

## Verification

```bash
bash apps/argus/scripts/test.sh
make test-agent
make test-gateway
make typecheck-agent
git diff --check
```

Use disposable `*_test` PostgreSQL only. The test runner creates its own temporary container
if `ARGUS_TEST_DATABASE_URL` is absent; it never uses production DB fixtures. Pytest does
not call paid models or real platform accounts.

Explicit paid synthetic checks:

```bash
apps/argus/.venv/bin/python -m apps.argus.test.manual_jev_semantic --authorize-real-model
apps/argus/.venv/bin/python -m apps.argus.test.manual_real_model --authorize-real-model --output /tmp/new-private-run
```

The discovery-only smoke needs no DB and can optionally exercise a configured fallback:

```bash
# perception must be an existing named model key in the selected Agent settings.
apps/argus/.venv/bin/python -m apps.argus.test.manual_real_scenarios \
  --authorize-real-model --mode discovery --discovery-initial-only \
  --fallback-model perception --max-semantic-requests 6 --max-fallback-requests 6 \
  --output /tmp/new-private-discovery-run
```

It keeps the six initial requests unchanged and records selected_route/native_confidence,
primary/final decisions and separate fallback attempts. The broader discovery matrix also
covers existing candidates, a different topic from the same source, quotes, negation,
chatter and ambiguity. Reports explicitly mark durable workflow/full pipeline as not executed;
classification success is not proof of Channel creation or business completion. If no fallback
is triggered, `fallback_exercised` stays false. Authentication/permission errors stop the run.
Proxy variables can be inherited; use NO_PROXY for a general-model host only when that host
requires direct access. Do not silently retry with different credentials or routing.

The full smoke requires a disposable test DB URL and real configured JEV. It no longer
substitutes DeepSeek for semantic. For a larger multi-scenario trial, use:

```bash
# Set ARGUS_TEST_DATABASE_URL to a disposable *_test PostgreSQL database for full mode.
apps/argus/.venv/bin/python -m apps.argus.test.manual_real_scenarios \
  --authorize-real-model --mode full --proxy http://127.0.0.1:7890 \
  --output /tmp/argus-new-private-acceptance \
  --max-agent-requests 240 --max-scenario-requests 80 \
  --max-semantic-requests 120 --scenario-timeout 600
```

The new output directory must not already exist. Six scenarios cover review, medical
appointment, software release, delivery, payment status (no actual payment), and repair.
A 24-case real JEV matrix tests positive paraphrases, negation, quotations and unrelated
messages first. Only a connected and fully correct matrix starts the real PG/TCP/Agent
pipeline. `--mode semantic` needs no DB; repeat `--scenario <name>` to select pipeline
cases. Semantic matrix attempts are deducted from the total semantic budget.

Authentication/permission errors stop immediately; an unsettled business round stops
before starting another paid task. Reports distinguish actual model/semantic responses
from persisted business completion. Output is private (directory0700/files0600), excludes
credentials and raw error bodies. A failed scenario is never converted to a pass by
retrying it invisibly. An earlier OpenRouter preflight through7890 returned
PermissionDeniedError and did not reach its matrix or business scenarios. Subsequent
classification evidence and the current discovery-fallback permission blocker are recorded
separately in [the discovery execution record](docs/spec/2026-11-02-decision-script-atomic-commit/plan-discovery-fallback-execution.md);
none of these classification tests proves the six full business scenarios passed.

The older smoke entry retains defaults agent120 total/50 per scenario and semantic64;
both have scenario600s, output3072 tokens/request and explicit budgets. Usage counts are
not a verified monetary bill. Last SDK-default authentication failure and corrected
OpenRouter permission failure are recorded separately, not treated as the same diagnosis.

Design: [new spec](docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md).
Historical prototype/partial paid evidence remains in the old feature directory. Remaining
limitations include single-instance scheduling, no automatic artifact retention GC, no real
platform write tools, and no encrypted Feishu envelope support.
