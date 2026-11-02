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
  The default route/model match the repository's Persona JEV configuration:
  `https://openrouter.ai/api` and `typesafe/jev-1.13`; explicit Argus overrides take precedence.
  Missing key means semantic unavailable, including default semantic discovery. The key must
  be accepted by the selected route; key presence is not proof of working authentication.
- `ARGUS_SAFETY_REVIEW_ENABLED` (default false), `ARGUS_SAFETY_REVIEW_BUDGET` (default16):
  optional fail-closed typed LLM review before publishing candidate commit rules; static lint never invokes it.
- `ARGUS_DISCOVERY_ENABLED`, `ARGUS_DISCOVERY_RULE_PATH`: ongoing discovery and optional replacement script.
- Existing Mem0/relationship/OpenKB endpoint/token configuration provides read-only evidence.
  Memory also requires `ARGUS_MEMORY_USER_ID`, `ARGUS_MEMORY_AGENT_ID`, `ARGUS_MEMORY_WORKSPACE_KEY`;
  knowledge requires `ARGUS_KNOWLEDGE_BASE`. Scripts cannot override trusted scopes.

Keep all credentials in ignored environment configuration, never rule source or prompts.

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
2. Existing Channel rules and ongoing new-topic discovery run independently; a subscribed
   source is not exclusively owned by one Channel. Discovery uses trusted candidate summaries,
   typed Choice/confidence and bounded retries. Ambiguous decisions defer.
3. Bridge freezes passed observations, sends a bounded current Channel snapshot and SDK
   metadata through the authenticated Gateway to the normal Agent execution chain.
4. Agent may combine snapshot, history and authorized memory reads. It need not always
   read Channel first or search known tools again. Real session registry/allowlist remains authoritative.
5. Agent calls `argus_commit(results, progress?, source_keys?, rule_source?, lifecycle?)`.
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
The transaction does not roll back external service actions; arbitrary write tools are not opened.

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
retrying it invisibly. Current real preflight on OpenRouter through7890 returned
PermissionDeniedError; the 24-case matrix and six paid business scenarios have not passed.

The older smoke entry retains defaults agent120 total/50 per scenario and semantic64;
both have scenario600s, output3072 tokens/request and explicit budgets. Usage counts are
not a verified monetary bill. Last SDK-default authentication failure and corrected
OpenRouter permission failure are recorded separately, not treated as the same diagnosis.

Design: [new spec](docs/spec/2026-11-02-decision-script-atomic-commit/plan-design.md).
Historical prototype/partial paid evidence remains in the old feature directory. Remaining
limitations include single-instance scheduling, no automatic artifact retention GC, no real
platform write tools, and no encrypted Feishu envelope support.
