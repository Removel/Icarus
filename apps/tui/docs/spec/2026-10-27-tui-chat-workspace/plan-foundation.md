# TUI Chat Workspace Foundation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or the user-selected superpowers:subagent-driven-development. Execute checkbox steps in order. No automatic Git mutations.

**Goal:** Make the existing TUI genuinely decoupled and multi-page-ready before changing its appearance.

**Architecture:** Explicit dependencies enter through main; client code is grouped by service object. A retained ChatScreen reads an app-owned Store. Framework-free ChatSession decides transitions and returns finite I/O operations; App workers execute them and post captured-identity results back to the App's single serial message path.

**Tech Stack:** Existing Python/Textual/Rich/websockets/pytest dependencies; no additions.

**Spec:** [design.md](design.md); global constraints and review focus are inherited from [plan.md](plan.md).

## Global Constraints

- Appearance, existing keyboard behavior, correction grouping and timeline semantics stay unchanged in this phase.
- No Agent imports, protocol extension, new production page, psutil, or generic dispatch/event framework.
- Each stored fact has one owner; live/historical/rebuilt paths apply a presentation action once, not once per layer.
- Non-UI modules do not import Textual; I/O does not mutate shared state after await.
- Existing behavior tests are migrated, not discarded to accommodate new interfaces. Preserve native assert and synchronous tests using asyncio.run where the app environment lacks pytest-asyncio.
- No commit, push, branch or merge without a new explicit request. Run focused tests before each full TUI suite.

## Review Focus

1. Queue state transitions performed inside an async worker can overtake early-update projection: reproduce existing handoff tests before moving code (Tasks 3–4).
2. A task status response from an earlier epoch of the same Session is stale even if session_id and task_id match (Task 4).
3. Session replacement and slow old-client cleanup must not block App messages or orphan an unadopted candidate (Tasks 4–5).
4. Cancelling an asyncio.to_thread await does not stop an OS clipboard read/file writer (Task 2).
5. Retained pages need revision reconciliation, not hidden duplicate rendering or App proxy state (Task 5).

---

## Task 1: Object-scoped Gateway Client and Explicit Assembly

**Files**
- Move `apps/tui/src/gateway_client/{__init__,client,ports,models}.py` to `apps/tui/src/client/gateway/` and create package `apps/tui/src/client/__init__.py`.
- Create `apps/tui/src/client/gateway/subscription.py` by extracting UpdateSubscription.
- Modify imports in `apps/tui/src`, `apps/tui/scripts/replay_events.py`, and tests; move `test/gateway_client/` to `test/client/gateway/`.
- Modify `apps/tui/src/main.py`, `apps/tui/src/screens/chat.py`, `apps/tui/test/test_cli.py`, `test/test_layering.py`, `test/test_ports.py`.

**Interfaces**
- `client.gateway.client.GatewayClient` keeps all existing runtime methods and behavior.
- `client.gateway.models` contains existing frozen SubmitAccepted and TaskOperationResult only.
- `client.gateway.ports.RuntimeClient` keeps the current callable contract; define `UpdateSubscriptionPort(Protocol)` with `async next_update() -> RuntimeUpdateModel`, `close() -> None` so fakes need not inherit the concrete queue.
- `ClientFactory = Callable[[str | None, bool], Awaitable[RuntimeClient]]` stays object-specific.
- main remains the only concrete default client-factory constructor. Screen/App accept injected dependencies; no silent fallback constructing a Gateway client.

- [ ] **Step 1 — Record actual baseline and add structural tests.** Capture current `make test-tui` failure identities outside tracked files. Add `test/client/gateway/test_imports.py::test_gateway_imports_without_ui_or_agent` using a subprocess: import public client/ports; assert no `textual`, `agent_app`, or model SDK module loaded. Add ports test with a fake subscription not derived from UpdateSubscription. Add a layering test asserting the new client/gateway path exists and no imports retain `src.gateway_client`.
- [ ] **Step 2 — Watch new tests fail.** Run `apps/tui/.venv/bin/python -m pytest apps/tui/test/client/gateway/test_imports.py -q`; expected missing new package/port.
- [ ] **Step 3 — Move, split, wire.** Reuse queue implementation unchanged. Update return annotations to subscription port, exports and every import/monkeypatch target. Remove `_default_runtime_factory` from Screen; preserve main's deferred loading behavior tested by CLI. No backward re-export shim under the old path.
- [ ] **Step 4 — Verify.** Run `apps/tui/.venv/bin/python -m pytest apps/tui/test/client/gateway apps/tui/test/test_cli.py apps/tui/test/test_ports.py apps/tui/test/test_layering.py -q`, then `make test-tui`, compile and diff check. Expected no new baseline failures.

## Task 2: One Attachment Owner and Pure Submission Decisions

**Files**
- Create `apps/tui/src/services/attachments.py` from clipboard_service + current resource helpers.
- Modify `domain/submission.py`, `domain/commands.py`, `widgets/composer.py`, `services/dispatch_service.py`, `screens/chat.py`.
- Delete `services/clipboard_service.py`, `services/submission_router.py` after consumers migrate.
- Create `test/services/test_attachments.py`; move router cases to `test/domain/test_commands.py`; mirror `test/domain/{test_chat_state,test_submission}.py` and `test/platform/test_clipboard.py`.

**Interfaces**
- `AttachmentsService(resource_root: Path | None, *, read_image: Callable[[], ClipboardImage | None], read_text: Callable[[], str | None])` owns file creation and deletion.
- `async read() -> ClipboardRead`: returns `image: DraftImage | None`, `text: str | None`; does not edit Composer. `None/None` retains Textual clipboard fallback; empty text remains distinct.
- `resources(submission: PendingMessage) -> tuple[ResourceRefModel, ...]`, `release(images: tuple[DraftImage, ...]) -> None`, `async close() -> tuple[str, ...]`.
- Internally track started to_thread operations; once close starts no new temp directory may be created. Finish/collect in-progress write results before directory cleanup, including a result whose consumer was cancelled.
- `Composer.ImagesReleased(images)` reports when clear/discard no longer references owned images. Submit/restore transfers the same PendingMessage identity without releasing queued images.
- In `domain.commands`: retain finite Enqueue/RunCommand/Reject/Unavailable values and add `route_submission(submission: PendingMessage, commands: CommandRegistry, *, phase: RuntimePhase, can_run_session_command: bool) -> SubmissionDecision` using current router rules. No Gateway reference or resource IO in domain.

- [ ] **Step 1 — Write RED resource and routing tests.** Port existing resources/marker/owned-file assertions. Add `test_domain_submission_has_no_disk_or_wire_adapter`, `test_clear_draft_emits_release_without_unlink`, `test_queued_attachment_not_released_on_submit`, `test_cancelled_clipboard_read_is_drained_before_close`. For the last, fake reader blocks on threading.Event; start read, cancel caller, begin close, release reader, assert close finishes with no directory/files created afterward. Test retained shared files, paths outside root, stable resource order, and text fallback once.
- [ ] **Step 2 — Run new tests.** `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/test_attachments.py apps/tui/test/domain -q`; expected missing service/interface or forbidden old side effect.
- [ ] **Step 3 — Implement the owner and delegate.** Move all unlink/resource mapping out of domain and Composer, replace duplicated dispatch helpers with AttachmentsService. IO failures propagate/return according to current fatal vs warning semantics; do not pretend close terminated a worker thread. Route image-release messages through the explicit host path.
- [ ] **Step 4 — Verify.** Run attachments/domain/platform/Composer tests and the existing `test_app.py` image paste/retry/steer/restore tests, then full TUI suite. Expected no resource leak and no new failure identity. Do not add a general filesystem abstraction or new clipboard dependency.

## Task 3: Store Owns Projection; ViewModel Only Derives Values

**Files**
- Move `widgets/conversation_projection.py` to `stores/conversation.py` and tests to `test/stores/test_conversation.py`.
- Create `stores/{__init__,chat}.py`, `viewmodels/{__init__,chat}.py`, corresponding `test/stores/test_chat.py`, `test/viewmodels/test_chat.py`.
- Modify `widgets/conversation.py`, `screens/chat.py`, `services/update_stream.py`, `test_app.py`, widget test harnesses and imports.

**Interfaces**
- Preserve ConversationProjection, Turn, DisplayUnit algorithms and identities in this phase (including current correction-in-card).
- `ChatStore` owns `interaction: ChatState`, `conversation: ConversationProjection`, `generation: int`, `revision: int`, `last_change: ChatChange | None` and explicit status/metadata fields.
- `ChatChange` is frozen: `revision: int`, `generation: int`, `actions: tuple[UiAction, ...]`, `history_reset: bool`. Revision monotonically increases for state-only changes too; last_change is only the latest batch, not a growing action log.
- `apply_actions(actions, *, historical=False) -> ChatChange`, `reset_session(*, generation: int) -> ChatChange`, `changed() -> ChatChange` are Store write methods called by the running coordinator (Screen temporarily, ChatSession in Task 4).
- `build_chat_viewmodel(store: ChatStore) -> ChatViewModel` exposes the fields listed in design §6; unknown model/reasoning/context/version and latest-version `未检查` are explicit values. No Textual/IO/client imports.
- `ConversationView(projection: ConversationProjection, workspace_path: Path, ...)` reads the provided projection. `async apply_change(change: ChatChange) -> None` only updates mounted view; `async reconcile(snapshot: ConversationWindowSnapshot) -> None` rebuilds from a captured stable bounded window. `ConversationWindowSnapshot` is frozen metadata `(generation, revision, start, end, copied_units)` produced synchronously by `ChatStore.capture_window(start: int, end: int)`; copy only bounded visible units (raw strings may be shared), release after rendering. One view-local serialized renderer owns `rendered_revision`; accept only contiguous captured batches, otherwise snapshot. Store advancing during awaited render schedules a new pass and cannot cause a delta already in the snapshot to append again. No acknowledgement until that captured pass finishes; remove internal projection.apply calls from live and deferred branches.
- Existing projection-based widget tests construct a Store and call its apply_actions before applying change to view. Do not leave an alternate apply-and-render convenience path in production.

- [ ] **Step 1 — Add RED single-application tests.** `test_action_applied_once_when_view_attaches`, `test_background_changes_do_not_construct_widgets`, `test_skipped_revision_reconciles_complete_text`, `test_store_advances_during_snapshot_render_without_duplicate_delta`, `test_history_batch_keeps_order_without_mounting_all_turns`, `test_viewmodel_unknowns_not_demo_values`. Suspend renderer between two awaits, advance Store, assert final complete text once and rendered_revision equals actual completed snapshot/batch, not a newer notification. Port window tests for 24-turn window, active tail, and manual disclosure persistence to external projection ownership.
- [ ] **Step 2 — Run.** `apps/tui/.venv/bin/python -m pytest apps/tui/test/stores apps/tui/test/viewmodels -q`; expected missing types or old view ownership.
- [ ] **Step 3 — Move data, remove double reducers.** Keep one semantic mutation path. Rendering may keep Widget indexes but cannot independently decide final/progress identity differently from projection. Store retains raw text, not Widget refs; ViewModel does not copy transcript on every token. Visibility resumes use generation/revision to choose incremental vs rebuild.
- [ ] **Step 4 — Verify.** Run stores/viewmodels/conversation widget tests plus existing early flush/reconnect/history integration tests, then full suite. Current appearance, CJK and correction semantics must remain unchanged.

## Task 4: App-owned ChatSession and Captured Result Ordering

**Files**
- Create `services/chat/{__init__,session,operations,dispatch,updates}.py` by extracting existing service logic.
- Move root `messages.py` to `screens/chat/messages.py`; create `screens/chat/__init__.py` and move Screen implementation to `screens/chat/screen.py` (first adapt imports, then simplify).
- Modify `app.py`, `main.py`, `stores/chat.py`, `tools` consumers later in Task 5.
- Delete old flat `services/{session_service,dispatch_service,update_stream,types}.py` and old root `messages.py` after references/tests migrate.
- Create `test/services/chat/{test_session,test_dispatch,test_updates,test_operations}.py`, `test/test_app_runtime.py`, move corresponding service tests.

**Interfaces and wire contract**
- `OperationScope(generation: int, epoch: int, client: RuntimeClient | None, operation_id: str)` frozen; attach same scope to completion. Increment Session generation on every adopted preparation/reset even when string IDs are equal. Increment epoch and invalidate old reader before replacement-connection I/O; reconnect operation uses that new epoch. Identity is not replaced by string id comparisons alone. Validate current pending operation_id as well as generation/epoch/client/reservation; allow old-resource cleanup completions only to settle their cleanup slot, never to change active state.
- Finite operation/result pairs in `operations.py`: PrepareSession/SessionPrepared; Dispatch/DispatchCompleted; CancelTask/CancelCompleted; Reconnect/Reconnected; QueryTaskStatus/TaskStatusCompleted; ListSessions/SessionsListed; ReadClipboard/ClipboardReadCompleted; CloseClient/ClientClosed; DiscardEmptySession/SessionDiscarded. Each carries only needed original arguments plus scope; Dispatch also carries exact reservation. Clipboard carries request_id and draft_token in addition to Session generation; its source is AttachmentsService, so client may be None. `async execute(operation) -> Completion` catches ordinary I/O failure into error, re-raises CancelledError, never writes Store. Preparation cleanup must return/close candidate on failure and stale adoption.
- Stream is not a finite `execute` operation: ChatSession exposes `subscription: UpdateSubscriptionPort | None` and `scope: OperationScope`; after each transition App synchronizes one read worker by identity. Reader posts `RuntimeOutputReceived(update, scope)` or `SubscriptionFailed(scope, error)`; it never applies projection. Do not create a parallel global bus.
- `ChatSession(store, client_factory, attachments, projectors, commands)` has `start/submit/cancel_active/request_resume/resume/clear/receive/complete/begin_shutdown/close` as in design §6. `request_resume` creates request_id and fetches list; `SessionsListed` preserves that token and captured source identity. `resume(session_id: str | None, *, request_id: str)` handles both selection and cancellation, validates/retires token and restores idle on cancellation. Preparation after selection rechecks captured source client's idle status before adopting target. SessionPicker is UI-only; a result while another page is active waits for chat resume or is invalidated, never pushes a picker over an unrelated page.
- `interrupt(has_draft) -> InterruptAction` retains precedence. Screen clears/restores draft locally; queue-pop/active cancel/exit are sent to the App intent handler, not performed by arbitrary Widget callbacks.
- UI messages: SubmitRequested(submission), InterruptRequested(has_draft), ResumeRequested(session_id: str | None, request_id), ClearRequested, ExitRequested(return_code), PasteRequested(request_id, draft_token), ImagesReleased(images), DisclosureChanged(generation, unit_id, expanded); runtime messages: OperationCompleted(result), RuntimeOutputReceived(update, scope), SubscriptionFailed(scope, error); view messages ChatChanged(revision), ClipboardReady(request_id, draft_token, result). `ChatSession.paste(*, request_id, draft_token)` creates the read operation. Composer `draft_token: int` advances on clear/discard/submission transfer/Session reset, but not ordinary typing; clipboard completion after those lifetime changes must not attach to a different draft. Clipboard delivery validates generation/draft_token, releases stale/undelivered owned files, and never steals focus from another page; hidden retained Composer defers delivery until visible and token still current. These are explicit point-to-owner messages, not reflective name dispatch.
- UpdateProcessor in updates.py prepares and validates an action batch synchronously; commit cursor/completion markers only after Store applies successfully. An ignored stale record cannot write state. No on_actions/on_fatal callbacks, no awaiting rendering inside stream processor.

- [ ] **Step 1 — Write RED ordering and lifecycle tests.** Preserve intent of `test_early_sequence_flush_and_live_updates_share_message_loop`, reconnect-history/live and activation-history/live tests. Assert 146 is stored before 147 even while view rendering is suspended. Add `test_task_status_from_previous_epoch_same_task_is_ignored`, `test_dispatch_retry_keeps_submission_id_and_target`, `test_old_candidate_closes_once_without_replacing_active_client`, `test_slow_close_does_not_block_input_or_current_updates`, `test_close_mid_prepare_releases_late_candidate`, `test_resume_cancel_retires_request_and_restores_idle`, `test_stale_picker_selection_and_source_busy_after_selection_are_rejected`, `test_clipboard_completion_after_clear_releases_owned_image`, `test_hidden_page_receives_no_clipboard_focus_or_picker`.
- [ ] **Step 2 — Execute RED.** Run `apps/tui/.venv/bin/python -m pytest apps/tui/test/services/chat apps/tui/test/test_app_runtime.py -q`; expected missing ChatSession/interfaces. Existing behavioral failures must be inspected rather than assertions relaxed.
- [ ] **Step 3 — Implement plain workflows and finite I/O.** Reuse current protocol error codes, submit/steer statuses and ChatState rules. Remove test-only `dispatch_next`; tests drive the same prepare/execute/complete seam as App. Session/dispatch helpers receive concrete dependencies, not lambda UI lookup or status/notify callbacks. Keep PreparedSession/error local to session workflow, not wire models.
- [ ] **Step 4 — Make App the serial host.** App constructs the one ChatSession/Store using main-supplied dependencies, schedules IO, validates every completion, drains early updates in the same transition before newer records, and posts view changes without awaiting widget rendering. Track task-status/cleanup workers as well as submit/subscribe. No result directly mutates state from Worker.
- [ ] **Step 5 — Remove App compatibility.** Move tests to explicit `app.chat_screen`/`app.chat_session`/`app.chat_store`. Delete __getattr__, __setattr__, query overrides, business-message forwarding, legacy workspace_path/initial_session_id constructor and `_tui_from_screen`. App constructor: `IcarusTextualApp(config: TuiConfig, *, client_factory: ClientFactory, attachments: AttachmentsService, commands: CommandRegistry | None = None, projectors: ProjectorRegistry | None = None)`; dependency-free `--help` behavior preserved in main.
- [ ] **Step 6 — Verify.** Run services/chat, app runtime, CLI, layering, conversation and Composer tests, then full suite. No new behavior differences permitted yet. Compare captured token identity explicitly, not only text output or result counts.

## Task 5: Multi-page Readiness, Tool Relocation and Test Cleanup

**Files**
- Modify `app.py`, `screens/chat/screen.py`, widget navigation/view reconciliation.
- Move `src/{replay,transcript}.py` to `src/tools/` and their tests to `test/tools/`; update scripts and golden test imports.
- Move page-focused `test_app.py` cases into `test/screens/chat/` (history, dispatch interaction, session picker, streaming/focus); retain only app lifecycle/route cases in `test_app.py` and shared runtime cases in test_app_runtime.py.
- Update README structure/commands, feature architecture only after code exists, and test/test_layering.py.

**Interfaces**
- App retains one `chat_screen: ChatScreen` and chat runtime for its lifetime. SecondaryScreen exists only in tests. Navigation uses Textual push/pop or retained named Screen; no generic registry needed.
- ChatScreen reads app-provided store and emits explicit messages. Suspend captures anchor/selection, sets view inactive, invalidates local activity_token and stops animated/render-only work; Resume reconciles if generation/revision changed and preserves detached reading position. In-flight render passes and deferred anchor/focus callbacks validate active + captured token/generation before and after awaits; abandoned passes leave dirty state and do not advance rendered_revision. This is a view lifetime token, not a new service epoch.
- `ChatSession.close` called only on final application exit or deliberate Session operation resource replacement, never on unrelated page switch. App runtime continues processing while another Screen is active.
- Widget disclosure changes use stable unit identity; full values stay in Store. A reset generation invalidates old anchor/disclosure intents safely.

- [ ] **Step 1 — Add RED cross-page tests.** `test_background_stream_survives_other_screen`, `test_resume_keeps_draft_cursor_and_detached_anchor`, `test_hidden_updates_do_not_mount_markdown`, `test_delayed_render_and_anchor_callback_invalidated_across_two_switches`, `test_navigation_during_reconnect_does_not_duplicate_consumer`, `test_app_exit_cancels_all_workers_and_closes_once`, `test_other_screen_ctrl_c_does_not_cancel_chat_task`. Use an actual secondary Screen in test harness, fake stream, and two page switches; assert only one live reader, original Composer object retained, complete background output visible once on return. Hold a render at await and a deferred focus callback before navigation; old work must not mount hidden nodes, acknowledge a newer revision or steal focus after returning.
- [ ] **Step 2 — Run.** `apps/tui/.venv/bin/python -m pytest apps/tui/test/screens/chat/test_navigation.py apps/tui/test/test_app_runtime.py -q`; expected old unmount ownership or missing suspension behavior.
- [ ] **Step 3 — Implement navigation ownership and tools move.** Do not add a production SettingsScreen placeholder. Keep Page/Widget local state in retained objects; restore on-screen anchor after layout/resize. Move helpers rather than copying test-only compatibility into product code. Offline replay still implements the same client port and sends no real request.
- [ ] **Step 4 — Enforce structure.** Import guards must catch normal and relative imports, not just a prefix substring. Verify client/domain/platform/stores/viewmodels/services forbid UI, domain forbids client/services/IO, widgets forbid services/client/platform; the one App composition path and Screen services contracts remain explicit. Avoid duplicate guard tests. Check removed paths no longer referenced by code/scripts.
- [ ] **Step 5 — Phase gate.** Run smallest affected tests, `make test-tui`, compile/diff. Run semantic replay; then headless real App with ReplayRuntimeService, explicitly submit fixture input, await terminal task, request shutdown, assert no uncaught exception/leftover reader. A plain timeout at welcome is not success. Record preserved visual states before entering UI phase; no new SVG baseline required.

## Phase A completion

The existing UI runs without App proxies, accidental worker-to-UI updates, duplicate queue/transcript storage, or page-owned network lifetime. Foundation can be reviewed and tested independently of the approved visual changes. Do not mark completion solely from a reduced file line count.
