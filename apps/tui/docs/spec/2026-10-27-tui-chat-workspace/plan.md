# TUI Chat Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native task-by-task implementation, or superpowers:subagent-driven-development only when chosen by the user. Follow the two phase plans below; no branch creation, commit, merge, push, or amend without a new explicit request.

**Goal:** Deliver the approved three-state chat UI on top of an app-owned, multi-page-ready Textual runtime with clear state, client, platform, and styling responsibilities.

**Architecture:** Keep existing Gateway contracts and queue/stream algorithms. First converge application-owned async handling and Store ownership while preserving appearance, then implement the approved chat layout and interactions. App owns long-lived workers and serial state application; Screens/Widgets own UI; framework-free ChatSession owns workflows; Store owns facts; ViewModel derives display values.

**Tech Stack:** Python 3.11+, Textual >=8.2.8,<9, Rich >=14.2,<15, asyncio, websockets, pydantic, pytest. No new psutil or browser runtime dependency.

**Spec:** [design.md](design.md). Approved prototype: [assets/demo.html](assets/demo.html).

## Global Constraints

- Only `apps/tui` product code changes; development skill references may be synchronized in the final documentation task. No Agent/Gateway/public protocol changes in this implementation.
- Preserve submit/steer/FIFO/LIFO/cancel/submission_id, real-model isolation, sequence/scope/epoch guards, complete/delta reconciliation, CJK, and windowed conversation behavior except explicitly approved display changes.
- Welcome, chat, and slash suggestions are states of one retained ChatScreen, not three Screens.
- Page navigation must not cancel the conversation, recreate the Composer, or lose draft/reading position.
- `client/gateway/` owns that service object's implementation and public ports. Do not create speculative other client objects or a generic SDK framework.
- Store is updated only through ChatSession on the App message pump. Network/attachment I/O workers return captured-identity results; they do not mutate Store or Widgets.
- Use existing public wire values; no duplicate provider-specific schema and no direct Agent imports.
- Do not reintroduce the deleted 13 SVG snapshots. Pilot and semantic tests are mandatory; real rendered visual review is a separate required check.
- Protocol-unavailable metadata remains unknown; demo-model, context percentages and fake version values must not ship as real data.
- Follow AGENTS.md validation order. Compare baseline failure identities, not counts; do not fix unrelated failures.
- Implementation starts by verifying intended remote `feature` baseline and the user's branch/workspace choice, without automatically merging or creating a worktree. The present feature documents do not authorize Git mutations.

## Review Focus

1. A previous connection epoch's completion after reconnect or Session generation replacement must not finish the new active task; test old epoch + same session/task id (foundation Task 4).
2. Updates preceding submit acknowledgement and live updates during slow historical rendering must be applied exactly once in original sequence (foundation Tasks 3–4).
3. Navigating away while a request completes must retain queue/draft/selection and return to the same reading anchor without creating hidden Markdown trees (foundation Task 5).
4. The slash panel's Enter/Tab/IME/newline and busy/attachment branches must not accidentally submit text or execute a command twice (UI Task 5).
5. Nested scroll regions, shrinking viewport, executing Thinking rebuilt from history, and cancelled clipboard threads must not lose content, steal focus, or leak resources (foundation Task 2; UI Tasks 4–6).

---

## Phase order and working deliverables

| Phase | Plan | Independently testable deliverable |
| --- | --- | --- |
| A — structure | [plan-foundation.md](plan-foundation.md) | Current appearance/semantics running through app-owned workflows and a retained Screen; tests prove multi-page readiness. |
| B — approved UI | [plan-chat-ui.md](plan-chat-ui.md) | Approved hero/transcript/input/menu layout, correction placement, execution disclosures, bounded local scrolling, split styles, real metadata fallbacks. |

Execute A before B. Do not combine protocol metadata expansion, settings pages, online updates, system monitoring, or real-model calls into either phase.

## Current-to-target map

| Current | Target / action |
| --- | --- |
| `src/main.py` and `screens/chat.py:_default_runtime_factory` | main is the only concrete dependency assembly path; remove duplicate default factory. |
| `src/app.py` proxies/forwarders | explicit `chat_screen`, app lifecycle/worker/serial result handlers; no magic forwarding. |
| `src/messages.py` | `screens/chat/messages.py`; explicit messages sent to actual owner, no `_tui_from_screen`. |
| `src/gateway_client/` | `src/client/gateway/`; split subscription implementation from value models. |
| `src/services/session_service.py`, `types.py` | `src/services/chat/session.py`; local PreparedSession/error types. |
| `src/services/dispatch_service.py` | `src/services/chat/dispatch.py`; remove separate test-only entry path. |
| `src/services/update_stream.py` | `src/services/chat/updates.py`; deterministic processor, no UI callback. |
| `src/services/submission_router.py` | finite decision values/validation in `domain/commands.py`. |
| `src/services/clipboard_service.py`, resource helpers and composer unlink | one `src/services/attachments.py` resource owner. |
| `widgets/conversation_projection.py` | `stores/conversation.py`; apply once before rendering. |
| queue/state/fatal flags scattered across Screen/services | `stores/chat.py` plus ChatSession connection ownership. |
| status and metadata display calculations | `viewmodels/chat.py`, pure frozen output. |
| `src/screens/chat.py` | `screens/chat/screen.py`, retained view; no transport/retry business branches. |
| `src/widgets/*` | `widgets/chat/*`, grouped by real chat feature; split Markdown/welcome/execution from message_cards. |
| `src/styles.tcss` | `styles/tokens.tcss`, `base.tcss`, `screens/*`, `widgets/*`. |
| `src/replay.py`, `transcript.py` | `src/tools/`, retained script and golden coverage. |

## Execution evidence

Executor records failing-before/passing-after focused tests and baseline comparison per task in a local ledger, not by modifying this document to claim future tests passed. Do not create new visual baselines by blindly accepting generated output. Do not repeat earlier assertions that a smaller App alone completes the refactor.

Before completion report:

```bash
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
git diff --check
```

Also run scripted semantic replay, a headless real-App replay with explicit input and shutdown, and visual checks at 120×40, 100×32, 70×24, 58×24, 100×12. The interactive replay script waits for input and manual exit; a timeout at the welcome page does not prove execution replay works.

## Documentation policy

`design.md` is the approved target contract. `arch.md` is not created as a placeholder; add or update implementation-derived architecture after the relevant code/tests exist. The 2026-10-26 document remains a historical first-round record; add a link to this feature rather than retroactively describing this unimplemented design as current code.

## Handoff

用户随后通过 `/goal` 明确批准 Native 实施及真实启动验收；当前实现与证据见 [arch.md](arch.md)、[validation.md](validation.md)。既有 commit 授权不延伸到本轮，本轮工作保持未提交、未推送。阶段执行中的工程裁定、平台未实测项和根启动器 Gateway readiness 限制在 validation 中逐项记录。
