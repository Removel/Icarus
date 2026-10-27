# TUI Structure Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. No commit without explicit user authorization.

**Goal:** Reduce `apps/tui/src/app.py` from a 1,879-line god object to a thin Textual App backed by a ChatScreen, framework-free services, pure models, and an immutable config object, without changing user-visible behavior.

**Architecture:** Organize by Textual runtime units instead of abstract layers: `App` owns process lifecycle and screen routing; `ChatScreen` owns page composition, focus, and child-message routing; `widgets/` own local rendering; plain `services/` own Gateway session, dispatch, update consumption, input routing, and clipboard; pure modules keep state machines, projections, and data types. Behavior is preserved by extracting one responsibility at a time and covering it with Pilot/widget tests and replay.

**Tech Stack:** Python 3.11+, Textual >=8.2.8,<9, pytest, asyncio.

**Spec:** [arch.md](arch.md)

> 执行记录（2026-10-27）：13 个 SVG 快照及其测试在本机 HEAD 已全部失败，已按用户要求删除；视觉与交互回归改由 Pilot/widget 测试和 replay 承担。首轮抽取完成时 `app.py` 163 行、全量 331 passed；`ChatScreen` 仍约 1,320 行，后续拆分见 arch 的“实施结果与后续”。

## Global Constraints

- Only `apps/tui` changes. Do not modify Agent, Gateway, `packages/gateway_protocol`, EventBus, Plugin Runtime, or model-provider code.
- No user-visible behavior, copy, keybinding, layout, queue semantics, or Session semantics changes. This is a behavior-preserving refactor.
- Keep the existing behavior covered by the TUI test suite, Pilot/widget tests, and replay. The 13 SVG snapshots and their tests were removed on 2026-10-27 because they failed at HEAD and the user no longer needs them.
- `services/**`, `gateway_client/**`, `chat_state.py`, `submission.py`, `commands.py`, and `event_pipeline/**` must not import Textual.
- `widgets/**` must not import `gateway_client`, `services`, or `app`.
- Only `Screen` / `Widget` create Textual workers. Services expose async methods and never call `run_worker`, `post_message`, `notify`, or `query_one`.
- Reuse `UiAction` as the domain-to-view vocabulary. Do not add a presenter, ViewModel, DI container, or TUI event bus.
- Tests mirror source paths; functional pytest with native `assert`.
- Do not fix unrelated baseline failures. Record the current failure set before starting and compare sets, not counts.
- Do not create branches, commit, amend, rebase, or push. Each task ends at a reviewed working-tree diff.
- Validation order per task: narrowest test file, affected TUI tests, `make test-tui`, `compileall`, `git diff --check`.

## Review Focus

1. After a transport error or reconnect, a queued message with an unknown outcome must retry with the same `submission_id` and must not duplicate the user message (Task 4 and Task 5 tests).
2. Updates that arrive after submission but before task acceptance must be buffered and replayed in sequence order, not dropped or reordered (Task 3 and Task 4 tests).
3. Switching or clearing a Session while a task is streaming must stop the old consumer and prevent old `task_id` updates from entering the new Session (Task 5 and Task 7 tests).
4. Extracting the page into `ChatScreen` must preserve scroll-follow detachment, Composer draft/cursor, and focus across streaming updates (Task 7 tests and widget tests; SVG snapshots removed).
5. Exit must cancel every worker, close the subscription/client, and discard only the empty Session it owns, without hanging or deleting a non-empty Session (Task 5 and Task 7 tests).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `apps/tui/src/config.py` (new) | `TuiConfig` and `build_config()`; no runtime state. |
| `apps/tui/src/gateway_client/ports.py` (new) | `RuntimeClient`, `ClientFactory` protocols moved out of `app.py`. |
| `apps/tui/src/services/__init__.py` (new) | Service package; re-export nothing unless a concrete caller needs it. |
| `apps/tui/src/services/types.py` (new) | `PreparedSession`, `SessionBusyError`, shared service result types. |
| `apps/tui/src/services/update_stream.py` (new) | Scope/sequence gates, projection, early updates, task-status reconciliation. |
| `apps/tui/src/services/dispatch_service.py` (new) | Reservation lifecycle, submit/steer, retry, blocked, fatal. |
| `apps/tui/src/services/session_service.py` (new) | Client lifecycle, Session start/resume/clear/reconnect, close. |
| `apps/tui/src/services/submission_router.py` (new) | Composer submission and command routing as a pure decision. |
| `apps/tui/src/services/clipboard_service.py` (new) | Image paste, temporary files, ResourceRef, cleanup. |
| `apps/tui/src/screens/chat.py` (new) | ChatScreen: layout, focus, child messages, page actions, result application. |
| `apps/tui/src/messages.py` (new) | Textual Messages used only at the Screen boundary. |
| `apps/tui/src/app.py` (modify) | Becomes App-only: CSS_PATH, lifecycle, push ChatScreen. |
| `apps/tui/src/main.py` (modify) | Build `TuiConfig` from args/cwd/data dir; run App with it. |
| `apps/tui/src/submission.py` (modify) | Add pure resource helpers moved from `app.py`. |
| `apps/tui/src/gateway_client/__init__.py` (modify) | Re-export moved protocols where existing callers expect them. |
| `apps/tui/scripts/replay_events.py` (modify) | Construct App with `TuiConfig`. |
| `apps/tui/test/test_config.py` (new) | Config resolution and defaults. |
| `apps/tui/test/services/*.py` (new) | Unit tests for each service using fake clients/subscriptions. |
| `apps/tui/test/screens/test_chat_screen.py` (new) | Screen composition, routing, and shutdown with fake services. |
| `apps/tui/test/test_layering.py` (new) | AST import-boundary assertions. |
| `apps/tui/test/test_app.py` (modify) | Shrinks to integration tests; service tests move to `test/services/`. Snapshot tests were deleted on 2026-10-27. |
| `apps/tui/test/test_app_snapshots.py` | Deleted on 2026-10-27 with its 13 failing SVG baselines; visual/interaction coverage uses Pilot/widget tests and replay. |
| `apps/tui/README.md`, `apps/tui/docs/spec/2026-10-26-tui-structure/arch.md` (modify) | Reflect final structure and responsibilities. |

---

### Task 1: Config Object and Baseline Capture

**Files:**
- Create: `apps/tui/src/config.py`
- Modify: `apps/tui/src/main.py`
- Test: `apps/tui/test/test_config.py`

**Interfaces:**
- Produces: `TuiConfig(workspace_path: Path, gateway_url: str = "ws://127.0.0.1:8765/rpc", session_id: str | None = None, resource_root: Path | None = None)`
- Produces: `build_config(*, cwd: Path, gateway_url: str, session_id: str | None, data_dir: Path) -> TuiConfig`
- For now `App` keeps its existing constructor; `main.py` passes config fields to it.

- [ ] **Step 1: Write failing config tests**

```python
# apps/tui/test/test_config.py
from pathlib import Path

from apps.tui.src.config import build_config


def test_build_config_resolves_workspace_and_resource_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    config = build_config(
        cwd=workspace,
        gateway_url="ws://127.0.0.1:9999/rpc",
        session_id="demo",
        data_dir=data_dir,
    )
    assert config.workspace_path == workspace.resolve()
    assert config.resource_root == (data_dir / "incoming").resolve()
    assert config.session_id == "demo"
    assert config.gateway_url == "ws://127.0.0.1:9999/rpc"


def test_build_config_keeps_default_url(tmp_path: Path) -> None:
    config = build_config(
        cwd=tmp_path,
        gateway_url="ws://127.0.0.1:8765/rpc",
        session_id=None,
        data_dir=tmp_path / "data",
    )
    assert config.session_id is None
    assert config.gateway_url == "ws://127.0.0.1:8765/rpc"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: apps.tui.src.config`.

- [ ] **Step 3: Implement `config.py` and wire `main.py`**

Implement the frozen dataclass and `build_config()`; resolve `cwd`, `data_dir / "incoming"`, and preserve the argparse default URL. In `main.py`, build the config once and pass its fields to the existing `IcarusTextualApp(...)` call. Do not change App yet.

- [ ] **Step 4: Run the config tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_config.py apps/tui/test/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Record the current full-suite baseline**

Run: `make test-tui`
Record the failing test set (names, not just counts). Do not fix unrelated failures.

- [ ] **Step 6: Stop for diff review**

Run: `git diff --stat apps/tui/src/config.py apps/tui/src/main.py apps/tui/test/test_config.py`
Do not commit.

---

### Task 2: Move Runtime Ports and Service Types Out of App

**Files:**
- Create: `apps/tui/src/gateway_client/ports.py`
- Create: `apps/tui/src/services/__init__.py`
- Create: `apps/tui/src/services/types.py`
- Modify: `apps/tui/src/gateway_client/__init__.py`
- Modify: `apps/tui/src/app.py`
- Test: `apps/tui/test/test_ports.py`

**Interfaces:**
- Produces: `RuntimeClient` and `ClientFactory` in `apps.tui.src.gateway_client.ports`.
- Produces: `PreparedSession(service: RuntimeClient, subscription: UpdateSubscription, history: SessionHistoryModel)` and `SessionBusyError` in `apps.tui.src.services.types`.
- Consumes: existing `SubmitAccepted`, `TaskOperationResult`, `UpdateSubscription` from `gateway_client.models`.

- [ ] **Step 1: Write a failing structural test**

```python
# apps/tui/test/test_ports.py
from apps.tui.src.gateway_client.ports import ClientFactory, RuntimeClient
from apps.tui.src.services.types import PreparedSession, SessionBusyError


def test_runtime_ports_import_without_app_module() -> None:
    assert RuntimeClient is not None
    assert ClientFactory is not None
    assert PreparedSession is not None
    assert issubclass(SessionBusyError, RuntimeError)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_ports.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Move the definitions and update imports**

Copy the `RuntimeClient` / `ClientFactory` Protocols into `gateway_client/ports.py`. Move `PreparedSession` and `SessionBusyError` into `services/types.py`. Remove the originals from `app.py` and import from the new modules. Re-export `RuntimeClient` from `gateway_client/__init__.py` if existing callers import it there.

- [ ] **Step 4: Run the structural test and affected suites**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_ports.py apps/tui/test/gateway_client apps/tui/test/test_app.py -q`
Expected: PASS. No behavior change.

- [ ] **Step 5: Stop for diff review**

Do not commit.

---

### Task 3: Extract UpdateStream

**Files:**
- Create: `apps/tui/src/services/update_stream.py`
- Modify: `apps/tui/src/app.py` (delegate to the new service)
- Test: `apps/tui/test/services/test_update_stream.py`

**Interfaces:**
- Produces: `UpdateStream(projectors, chat_state, *, session_scope, on_actions, on_fatal, on_user_input)`
- Produces: `UpdateFatal(stage: str, cause: BaseException)`
- Produces: `async def project(update, *, historical=False) -> tuple[UiAction, ...]`
- Produces: `def buffer(update) -> None`, `def take_early() -> tuple[RuntimeUpdateModel, ...]`, `def reset(last_sequence: int = 0) -> None`
- Produces: `async def reconcile(client, task_id) -> tuple[UiAction, ...]`
- `on_actions` is `Callable[[tuple[UiAction, ...]], Awaitable[None]]`; `on_fatal` is `Callable[[str, BaseException], None]`; `session_scope` returns `(session_id, workspace_key)`.

- [ ] **Step 1: Write failing UpdateStream tests**

Cover at least:

```python
async def test_sequence_gap_is_fatal() -> None:
    stream = make_stream()
    await stream.project(update(type="task.started", sequence=1))
    with pytest.raises(UpdateFatal):
        await stream.project(update(type="task.started", sequence=3))


async def test_early_updates_replay_in_sequence_order() -> None:
    stream = make_stream()
    stream.buffer(update(sequence=2, occurred_at=...))
    stream.buffer(update(sequence=1, occurred_at=...))
    assert [item.sequence for item in stream.take_early()] == [1, 2]


async def test_late_delta_after_assistant_message_is_ignored() -> None:
    ...
```

Also cover: inactive Session/workspace ignored, historical `SetRuntimeStatus` filtered, user input marks `on_user_input`, and `reconcile()` turns a completed task status into `FinishTurn`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_update_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: apps.tui.src.services.update_stream`.

- [ ] **Step 3: Move `_project_runtime_update`, `_flush_early_updates`, early buffering, `_reconcile_task_status`, and the completion set into the service**

Replace the App methods with thin delegates that call the service and post the same results. Keep `RuntimeOutputReceived` / `TaskStatusReceived` message flow until Task 7. Fatal conditions become `UpdateFatal`; App catches them in its message handler and enters the existing fatal path.

- [ ] **Step 4: Run focused and affected tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_update_stream.py apps/tui/test/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full TUI suite**

Run: `make test-tui`
Expected: same failure set as the Task 1 baseline.

- [ ] **Step 6: Stop for diff review**

Do not commit.

---

### Task 4: Extract DispatchService and Resource Helpers

**Files:**
- Create: `apps/tui/src/services/dispatch_service.py`
- Modify: `apps/tui/src/submission.py` (pure resource helpers)
- Modify: `apps/tui/src/app.py` (delegate)
- Test: `apps/tui/test/services/test_dispatch_service.py`
- Test: `apps/tui/test/test_submission.py` (resource helper cases)

**Interfaces:**
- Produces: `DispatchOutcome(accepted_task_id: str | None = None, refresh_queue: bool = True, schedule_next: bool = False, retry: DispatchReservation | None = None, reconnect_cause: BaseException | None = None, fatal_stage: str | None = None, fatal_error: BaseException | None = None)`
- Produces: `DispatchService(chat_state, client, *, resource_root, on_status, on_notify)` with `async dispatch_next(*, epoch) -> DispatchOutcome` and `async cancel_active(*, epoch) -> DispatchOutcome`.
- Produces in `submission.py`: `submission_resources(submission, resource_root) -> tuple[ResourceRefModel, ...]` and `delete_submission_images(submission) -> None`.

- [ ] **Step 1: Write failing dispatch tests**

Use a fake `RuntimeClient` and assert the outcome for every branch:

```python
async def test_steer_not_running_returns_to_queue() -> None:
    service = make_dispatch_service(steer_status="not_running")
    outcome = await service.dispatch_next(epoch=1)
    assert outcome.schedule_next is True
    assert outcome.fatal_error is None


async def test_transport_error_marks_outcome_unknown() -> None:
    service = make_dispatch_service(submit_error=GatewayTransportError("lost"))
    outcome = await service.dispatch_next(epoch=1)
    assert service.chat_state.dispatch_reservation is not None
    assert service.chat_state.dispatch_reservation.outcome_unknown is True
```

Cover: submit accepted; steer `accepted`, `already_finished`, `not_found`, `not_running`, `already_cancelling`, `invalid_content`, `expired`; gateway error codes `invalid_resource`, `resource_unavailable`, `invalid_content`, `expired`; unknown status fatal; missing client fatal; cancel `accepted` / `already_cancelling` / `already_finished` / failure.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_dispatch_service.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Move dispatch logic and resource helpers**

Move `_dispatch_next`, `_send_reservation`, `on_dispatch_finished`, `_handle_steer_result`, `_handle_dispatch_gateway_error`, `_block_dispatch`, `_handle_unknown_dispatch`, `_cancel_active_task`, `_resource_id`, `_submission_resources`, and `_delete_submission_images` into the service/pure helpers. App's remaining message handler applies the returned outcome and performs queue refresh, early-update flush, reconciliation, and reconnect as before.

- [ ] **Step 4: Run focused and affected tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_dispatch_service.py apps/tui/test/test_submission.py apps/tui/test/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full TUI suite**

Run: `make test-tui`
Expected: same failure set as baseline.

- [ ] **Step 6: Stop for diff review**

Do not commit.

---

### Task 5: Extract SessionService

**Files:**
- Create: `apps/tui/src/services/session_service.py`
- Modify: `apps/tui/src/app.py` (delegate)
- Test: `apps/tui/test/services/test_session_service.py`

**Interfaces:**
- Produces: `SessionResult(kind, prepared: PreparedSession | None = None, error: BaseException | None = None, message: str = "")`
- Produces: `SessionSelector = Callable[[tuple[SessionSummaryModel, ...], str], Awaitable[str | None]]`
- Produces: `SessionActivator = Callable[[PreparedSession], Awaitable[bool]]`
- Produces: `SessionService(runtime_factory, config, *, on_status)` with properties `client`, `subscription`, `epoch`, `session_id`, and methods `start`, `resume(select_session, activate)`, `clear(activate)`, `reconnect(last_sequence)`, `close`.

- [ ] **Step 1: Write failing session tests**

Cover at least:

```python
async def test_start_prepares_client_subscription_and_history() -> None:
    service = make_session_service()
    result = await service.start(None)
    assert result.kind == "ready"
    assert result.prepared is not None
    assert result.prepared.history.records == ()


async def test_failed_candidate_is_closed_and_empty_session_discarded() -> None:
    fake = FakeRuntimeFactory(fail_on_history=True)
    service = make_session_service(fake)
    with pytest.raises(SessionBusyError):
        ...
    assert fake.candidate.closed is True
    assert fake.candidate.discarded_session_id is not None


async def test_reconnect_loads_history_after_last_sequence() -> None:
    service = make_session_service()
    result = await service.reconnect(last_sequence=7)
    assert result.kind == "reconnected"
    assert fake.client.history_after_sequence == 7
```

Also cover: `resume` rejects a busy Session without activating; selector returns `None` → ready/cancelled, no swap; activation success closes the previous client and discards only a previously empty Session; `clear` on an empty Session returns the "already empty" message; `close()` collects errors instead of raising; reconnect failure returns `kind="failed"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_session_service.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Move session lifecycle code**

Move `_start_runtime`, `_prepare_session`, `_resume_session`, `_clear_session`, `_reconnect_runtime`, the client/subscription ownership fields, and the session-related cleanup. Keep `_apply_prepared_session` in App for now as the `activate` callback; pass it into `SessionService.resume/clear`. App applies returned history and swaps the update consumer exactly as today.

- [ ] **Step 4: Run focused and affected tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_session_service.py apps/tui/test/test_app.py apps/tui/test/screens/test_session_picker.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full TUI suite**

Run: `make test-tui`
Expected: same failure set as baseline.

- [ ] **Step 6: Stop for diff review**

Do not commit.

---

### Task 6: Extract SubmissionRouter and ClipboardService

**Files:**
- Create: `apps/tui/src/services/submission_router.py`
- Create: `apps/tui/src/services/clipboard_service.py`
- Modify: `apps/tui/src/app.py` (delegate)
- Test: `apps/tui/test/services/test_submission_router.py`
- Test: `apps/tui/test/services/test_clipboard_service.py`

**Interfaces:**
- Produces: `SubmissionPlan` variants `Enqueue(submission)`, `RunCommand(definition)`, `Reject(reason, title)`, `Unavailable(reason, title)`.
- Produces: `SubmissionRouter(commands).route(submission, *, phase, can_run_session_command) -> SubmissionPlan`.
- Produces: `ClipboardService(resource_root)` with `paste_image() -> DraftImage | None`, `resources(submission) -> tuple[ResourceRefModel, ...]`, `discard(submission) -> None`, `cleanup() -> list[str]`.

- [ ] **Step 1: Write failing router tests**

```python
def test_plain_message_enqueues() -> None:
    plan = make_router().route(
        PendingMessage("hello"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )
    assert isinstance(plan, Enqueue)


def test_command_with_arguments_is_rejected() -> None:
    plan = make_router().route(
        PendingMessage("/exit now"),
        phase=RuntimePhase.READY,
        can_run_session_command=True,
    )
    assert isinstance(plan, Reject)
```

Cover: unknown command, `/exit` command, command with attachments rejected, command requiring idle while busy returns `Unavailable` with the original draft restorable, and a message containing an image marker enqueues with the original submission.

- [ ] **Step 2: Write failing clipboard tests**

Cover: store a fake clipboard image and map markers in order; duplicate/removed markers drop images; `resources()` produces stable relative IDs and rejects paths outside the controlled root; `discard()` removes only owned temporary files; `cleanup()` removes the temporary directory and reports errors.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_submission_router.py apps/tui/test/services/test_clipboard_service.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Move routing and clipboard code**

Move command validation from `on_persistent_composer_submitted` / `_handle_command_submission` into the router. Move image paste handlers, `_store_clipboard_image`, `_paste_clipboard_image`, `_notify_image_paste_failure`, the temp directory, and resource assignment into the clipboard service. App keeps only: parse result, restore draft, notify, focus, enqueue, and command execution.

- [ ] **Step 5: Run focused and affected tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_submission_router.py apps/tui/test/services/test_clipboard_service.py apps/tui/test/widgets/test_composer.py apps/tui/test/test_app.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full TUI suite**

Run: `make test-tui`
Expected: same failure set as baseline.

- [ ] **Step 7: Stop for diff review**

Do not commit.

---

### Task 7: Introduce ChatScreen and Make App Thin

**Files:**
- Create: `apps/tui/src/screens/chat.py`
- Create: `apps/tui/src/messages.py`
- Modify: `apps/tui/src/app.py`
- Modify: `apps/tui/src/main.py`
- Modify: `apps/tui/scripts/replay_events.py`
- Modify: `apps/tui/test/test_app.py`, `apps/tui/test/test_cli.py`
- Test: `apps/tui/test/screens/test_chat_screen.py`

**Interfaces:**
- Produces: `ChatScreen(config: TuiConfig, *, runtime_factory: ClientFactory | None = None, command_registry: CommandRegistry | None = None, projector_registry: ProjectorRegistry | None = None)`.
- Produces: `IcarusTextualApp(config: TuiConfig, *, runtime_factory: ClientFactory | None = None)`, whose `on_mount()` pushes `ChatScreen`.
- Produces: `messages.py` containing the surviving Textual Messages: `ApplyActions`, `SessionPrepared`, `DispatchCompleted`, `TaskStatusReceived`, `SessionActivationRequested`, `RuntimeStartFailed`, `RuntimeSubscriptionFailed`, `RuntimeReconnected`.

- [ ] **Step 1: Write a failing ChatScreen test**

```python
async def test_chat_screen_composes_shell_and_focuses_composer() -> None:
    app = IcarusTextualApp(test_config(), runtime_factory=FakeRuntimeFactory())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ChatScreen)
        assert screen.query_one("#composer") is not None
        assert screen.query_one("#conversation") is not None
        assert screen.focused is screen.query_one("#composer")


async def test_chat_screen_applies_actions_without_touching_client() -> None:
    ...
```

Also cover resize classes (`-narrow`, `-compact-logo`, `-short`), session picker pushing, and `on_unmount` cancelling workers.

- [ ] **Step 2: Run the test to verify it fails**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/screens/test_chat_screen.py -q`
Expected: FAIL with `ModuleNotFoundError: apps.tui.src.screens.chat`.

- [ ] **Step 3: Move composition and page behavior into ChatScreen**

Move `compose`, `on_mount`, `on_resize`, `_update_responsive_classes`, widget message handlers, action application, queue/status rendering, interrupt/EOF/page actions, command execution, and shutdown orchestration. ChatScreen builds the services from `config` and owns all workers. App keeps `CSS_PATH`, `TITLE`, `on_mount` pushing the Screen, and a defensive `on_unmount` that asks the active ChatScreen to finish cleanup if it is still mounted.

- [ ] **Step 4: Update App construction sites**

Change `main.py`, `scripts/replay_events.py`, `test_app.py`, and `test_cli.py` to build `TuiConfig` and pass it. (The snapshot test module was deleted on 2026-10-27.) Update imports of moved messages to `messages.py`. Delete `RuntimeOutputReceived`, `DispatchFinished`, and other messages that no longer cross the new boundary.

- [ ] **Step 5: Run the ChatScreen and App tests**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/screens/test_chat_screen.py apps/tui/test/test_app.py apps/tui/test/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Run interaction tests instead of deleted snapshots**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/screens apps/tui/test/widgets -q`
Expected: PASS. SVG snapshots were removed; verify visual behavior with the replay shell and focused widget/screen tests instead.

- [ ] **Step 7: Run the replay shell**

Run: `apps/tui/.venv/bin/python apps/tui/scripts/replay_events.py --tui-real --speed 8 apps/tui/test/fixtures/synthetic_tui_events.jsonl`
Expected: the shell renders the synthetic conversation and exits without model calls.

- [ ] **Step 8: Verify the size and boundary**

Run:

```bash
wc -l apps/tui/src/app.py apps/tui/src/screens/chat.py
grep -n "GatewayClientError\|get_session_status\|\.submit(\|\.steer_task(" apps/tui/src/app.py
```

Expected: `app.py` ≤ 250 lines; no protocol/status branching or direct client operations in `app.py`.

- [ ] **Step 9: Stop for diff review**

Do not commit.

---

### Task 8: Split Tests, Add Import Guards, Update Docs

**Files:**
- Modify: `apps/tui/test/test_app.py`
- Create: `apps/tui/test/test_layering.py`
- Modify: `apps/tui/README.md`
- Modify: `apps/tui/docs/spec/2026-10-26-tui-structure/arch.md`

**Interfaces:**
- Produces: AST import guard for the layer contract.
- Produces: test files mirroring `src/services/`, `src/screens/`, `src/widgets/`.

- [ ] **Step 1: Move service tests out of `test_app.py`**

Move dispatch/session/update/command tests into the matching `test/services/` files created in Tasks 3–6. Keep in `test_app.py` only: App startup, Screen routing, exit/cleanup, and cross-service integration. Do not weaken assertions during the move.

- [ ] **Step 2: Write the failing layering test**

```python
# apps/tui/test/test_layering.py
from pathlib import Path
import ast

SRC = Path(__file__).parents[1] / "src"

FORBIDDEN = {
    "services": {"textual", "apps.tui.src.widgets", "apps.tui.src.screens", "apps.tui.src.app"},
    "gateway_client": {"textual", "apps.tui.src.services", "apps.tui.src.widgets", "apps.tui.src.screens", "apps.tui.src.app"},
    "widgets": {"apps.tui.src.gateway_client", "apps.tui.src.services", "apps.tui.src.app"},
}


def test_import_boundaries() -> None:
    for folder, prefixes in FORBIDDEN.items():
        for path in (SRC / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not any(node.module.startswith(p) for p in prefixes), (path, node.module)
```

Extend it to assert `chat_state.py`, `submission.py`, `commands.py`, and `event_pipeline/**` never import `textual`.

- [ ] **Step 3: Run the layering test and fix violations**

Run: `apps/tui/.venv/bin/python -m pytest apps/tui/test/test_layering.py -q`
Expected: PASS. If it fails, move the offending import behind a callback/port rather than weakening the test.

- [ ] **Step 4: Update docs**

Update `apps/tui/README.md`'s architecture section to name App/Screen/Widget/Service/Model and the new directories. Update `arch.md` so its "current architecture" section matches the implemented tree; keep the design rationale and mark the migration mapping as completed.

- [ ] **Step 5: Run the complete validation**

```bash
apps/tui/.venv/bin/python -m pytest apps/tui/test -q
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
git diff --check
```

Expected: same failure set as the Task 1 baseline; no new failures. SVG snapshots no longer exist.

- [ ] **Step 6: Stop for final review**

Report the before/after line counts for `app.py`, `screens/chat.py`, and each service; list any pre-existing failures; do not commit.

---

## Self-Review Notes

- **Spec coverage:** config (Task 1), ports/types (Task 2), update stream (Task 3), dispatch (Task 4), session (Task 5), input/clipboard (Task 6), screen/App (Task 7), tests/docs/guards (Task 8). The non-goals (no Ink, no protocol changes, no widget rewrite) are enforced by Global Constraints.
- **Behavior preservation:** each service task keeps App as a delegating adapter until Task 7; `test_app.py` and widget/screen tests guard the transition. SVG snapshots were removed on 2026-10-27.
- **Type consistency:** `PreparedSession`, `SessionResult`, `DispatchOutcome`, `SubmissionPlan`, `UpdateStream` signatures are defined once and referenced by later tasks.
- **Review Focus coverage:** queue/retry (Task 4), early updates (Task 3), Session switching/old task isolation (Tasks 5 and 7), scroll/draft/focus (Task 7 widget tests; snapshots removed), exit cleanup (Tasks 5 and 7).
- **Proportion:** tasks specify interfaces, test names, and move boundaries; they do not restate the implementation bodies.
