# TUI Chat Workspace UI Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or the user-selected superpowers:subagent-driven-development. Complete [plan-foundation.md](plan-foundation.md) first; no automatic Git mutations.

**Goal:** Implement the three approved target states in real Textual with reusable chat widgets, per-responsibility TCSS, and tested scrolling/input behavior.

**Architecture:** Retained ChatScreen composes ConversationArea and InputDock from chat widgets. The app-owned Store is the only semantic projection; ViewModel supplies honest status/metadata; widgets own editing, visibility and animation. Styles are explicitly loaded by App and scoped to their real page/widget owners.

**Tech Stack:** Existing Textual >=8.2.8,<9, Rich >=14.2,<15, Python 3.11+, pytest and deterministic replay. No HTML/browser runtime in the production TUI.

**Spec:** [design.md](design.md); approved reference [assets/demo.html](assets/demo.html). The outer browser preview controls and simulate button are not product UI.

## Global Constraints

- Preserve public wire contracts, queue and task semantics; visual changes do not authorize new backend requests.
- Keep live and window-rebuilt transcript ordering identical, stable task/step/call_id identity, default folded Thinking/tools, and existing CJK/full-delta reconciliation.
- Three UI states use one ChatScreen and the same Composer instance. Other feature pages are future work.
- Scope styles to ChatScreen/widget types; no browser CSS modules, @media, invented TCSS properties, or dependency on launch cwd.
- Missing model/reasoning/context/version display unknown; latest-version `未检查`. No demo values in product source.
- User removed the SVG baselines; visual review still requires rendered screenshots, not only Pilot assertions.
- No commit/push/branch unless explicitly requested. Focused tests precede full suite, compile/diff.

## Review Focus

1. Rebuilding an offscreen active Thinking must not restart completed animation or mark live reasoning finished (Tasks 3–4).
2. Corrections must visually move to user messages without becoming new tasks/navigation turns or reordering tools around Assistant text (Task 3).
3. Command Enter/IME/newline must not execute twice, submit a partial command, or lose attached images (Task 5).
4. Short-screen resize and panel expansion must not hide Composer/metadata or reset the detached history anchor (Tasks 2, 4–6).
5. Inner scrollers must consume only their own mouse events; Tool/Thinking collapse and source updates must not steal outer follow state or focus (Task 4).

---

## Task 1: Split Chat Widgets and TCSS Without Changing Appearance

**Files**
- Move existing widgets into `apps/tui/src/widgets/chat/`; update consumers and test directory to `apps/tui/test/widgets/chat/`.
- Split `widgets/chat/messages.py` (intermediate moved file) into `welcome.py`, `markdown.py`, `message_cards.py`, `execution_panel.py`.
- Create `widgets/chat/text.py` for message_preview and use from rail/queue.
- Create `styles/{tokens,base}.tcss`, `styles/screens/{chat,session_picker}.tcss`, `styles/widgets/{welcome,conversation,input_dock,command_menu}.tcss` as rules gain callers; delete old styles.tcss only once all rules loaded.
- Modify App CSS_PATH and test harness style paths. Do not leave an empty command_menu stylesheet: create with real widget in Task 5 if not yet present.

**Interfaces**
- Keep existing concrete widget public methods during the move; imports via `widgets.chat` may explicitly re-export caller-used classes but no legacy `widgets/messages.py` shim.
- `markdown.py` owns StreamingMarkdown, CJK content/paragraph adaptation and stream append notification used by message cards/Thinking/ConversationView.
- `message_cards.py` owns UserMessage, AssistantMessage/Progress, UserCorrectionBlock (to change in Task 3), ErrorMessage, TurnStatusMessage and shared streaming-message lifecycle.
- `execution_panel.py` owns current RunCard/ThinkingBlock/ToolBlock/disclosure. Keep all state/text methods; no copied CJK engine.
- App uses explicit file paths resolved against its own source path, shared in subclasses/tests. tokens precede the styles that use them; Screen.CSS_PATH is not relied upon for scoping.

- [ ] **Step 1 — RED tests for ownership and style loading.** Add `test/widgets/chat/test_imports.py` asserting Markdown private API imports live only in markdown.py, hero classes do not import tool execution, and new widget paths resolve. Add `test/screens/chat/test_styles.py::test_styles_load_from_unrelated_cwd` using monkeypatch.chdir and App.run_test, assert theme colors/dimensions and SessionPicker style match current before layout change; later file-scoped styles do not leak to a simple SecondaryScreen.
- [ ] **Step 2 — Run RED.** Run these files; expected new paths absent or old global Screen selectors affecting secondary page.
- [ ] **Step 3 — Move implementation and styles.** Reuse existing tests and private API workarounds without rewriting algorithms. Scope chat responsive classes to ChatScreen; base only carries true application defaults. Theme tokens retain current colors. Test each CSS property with installed Textual version rather than assuming browser support.
- [ ] **Step 4 — Verify.** Run all moved widget tests, session picker, CLI and replay imports; then `make test-tui`. Capture/render one current-looking chat frame and inspect it before proceeding. This is the no-appearance-change checkpoint.

## Task 2: InputDock, Two Status Regions and Persistent Hero

**Files**
- Create `widgets/chat/input_dock.py`, `widgets/chat/status.py`; adapt Composer/QueuePanel, welcome.py, conversation.py.
- Modify `screens/chat/screen.py`, `stores/chat.py`, `viewmodels/chat.py`, `configs/config.py`, relevant TCSS.
- Remove former status_bar.py after consumers migrate.
- Tests: `test/widgets/chat/{test_input_dock,test_status,test_welcome}.py`, `test/screens/chat/test_layout.py`, viewmodel and config tests.

**Interfaces**
- `InputDock` composes TaskStatusLine, QueuePanel, existing PersistentComposer, later CommandMenu, SessionMetadataBar. `show_viewmodel(vm: ChatViewModel) -> None` updates only the affected labels/visibility; no Composer recompose.
- TaskStatusLine reports current phase/activity/queued count; SessionMetadataBar reports model/reasoning/context/session/workspace separately; help hints are not concatenated into task-status text.
- `TuiConfig.app_version: str | None = None`; ChatStore metadata optional labels set only from explicit known input. ViewModel fallback: model/reasoning/version `未知`, context `—`, latest version `未检查`. No version network query.
- ConversationView leading WelcomeMessage exists for empty/first window (start=0), but not counted as a turn; removed when not in first window and rebuilt on returning. Do not duplicate hero after reset/reconcile.
- QueuePanel has one scroller, not nested VerticalScroll; when space is short use a single queue count/preview row.

- [ ] **Step 1 — RED layout tests.** `test_input_dock_keeps_composer_instance_and_selection`, `test_task_status_above_composer_metadata_below`, `test_hero_scrolls_with_transcript_and_returns_at_first_window`, `test_metadata_unknowns_not_mock_model`, `test_short_layout_keeps_minimum_three_conversation_rows`. Exercise 120×40, 100×32, 70×24, 58×24, 100×12. Assert IDs/order/regions, no offscreen input/status/meta, original text/selection after resize and updated state. Missing optional metadata must not force horizontal scroll.
- [ ] **Step 2 — Run RED.** Run input_dock/status/layout/viewmodel tests; expected missing widgets/layout/fields.
- [ ] **Step 3 — Implement layout.** Hero stays in transcript; ordinary bottom area follows design dimensions. Add bounded layout budget calculation in InputDock, not independent max heights that can collectively consume the screen. Preserve queue semantics and editable input while initializing/running. A new Screen is not created for welcome.
- [ ] **Step 4 — Verify.** Run layout/widget/ViewModel tests, current draft/focus/queue integration tests and complete suite. Inspect welcome and chat screenshots in wide and short sizes.

## Task 3: Chronological Execution Segments and User Corrections

**Files**
- Modify `stores/conversation.py`, `event_pipeline/actions.py` only for app-owned presentation fields if necessary (no wire change), `widgets/chat/{conversation,message_cards,execution_panel}.py`.
- Tests: `test/stores/test_conversation.py`, `test/widgets/chat/test_conversation.py`, `test/tools/test_timeline_transcript_golden.py` and existing golden only when expected semantic text intentionally changes.

**Interfaces**
- Existing `AppendUserCorrection(task_id, text, applied_before_step)` retains identity and does not become AppendUserMessage. Top-level DisplayUnit(kind="correction") in the same Turn, not RunCard.children.
- Correction closes the current visual execution segment; later activity creates a new segment without moving earlier Tool/Assistant chronology. Add stable `segment: int = 0` on run_card units; stable unit keys include task+segment, assistant/thinking task+step, tool task+call_id; corrections use task+occurrence ordinal within Turn (not raw text key).
- Store handles each action once; live view reads resulting units. No second reducer inside ConversationView. Disclosure state restored by stable identity.
- Set `DisplayUnit.completed = True` on CompleteThinking; FinishTurn terminates still-running Thinking, and running tools receive the actual terminal status. Do not rely on historical flag to infer completion.

- [ ] **Step 1 — RED chronology tests.** Sequence: user A → thinking step1 → tool start/complete → assistant complete step1 → correction → tool step2 → assistant complete step2 → finish. Assert exact top-level order, correction in original Turn, one rail turn, stable distinct execution segments, and same content after window eviction/rebuild. Add completed Assistant followed by same-task Tool, repeated call_id across tasks, and Tool completed without start.
- [ ] **Step 2 — Run RED.** `apps/tui/.venv/bin/python -m pytest apps/tui/test/stores/test_conversation.py apps/tui/test/widgets/chat/test_conversation.py -q`; existing correction-in-card assertions should fail until intentionally replaced by target assertions, not removed wholesale.
- [ ] **Step 3 — Implement projection-first presentation.** Reuse existing complete/delta and final-vs-progress rules. Tool records remain in chronological execution segments; render corrections with UserMessage-compatible visual variant and applied label; no new task or queue operation. Live and rebuilt paths consume the same units.
- [ ] **Step 4 — Verify.** Run store/widget/projector tests, replay golden and full suite. Update only exact intentional correction output/order assertions; preserve secret/safe-preview exclusions.

## Task 4: Local Scrolling, Thinking Animation and Rebuild Parity

**Files**
- Modify `widgets/chat/{execution_panel,message_cards,conversation,turn_rail}.py`, `stores/conversation.py` disclosure data and chat/conversation TCSS.
- Tests: `test/widgets/chat/{test_execution_panel,test_conversation,test_message_cards}.py`, `test/screens/chat/test_layout.py`.

**Interfaces**
- ExecutionPanel binds one run_card segment's Tool list and Thinking summaries; tools folded by default and safe previews only. list height grows with content up to 8 logical rows (short 3); no blank fixed large panel when empty/short.
- Long message body grows up to 12 rows (short 4), then local scroll; actual full source stays in Store. Plain user text must not interpret Rich markup.
- Local scroll consumes wheel only if offset can move; if already at that edge propagate to next scroll owner. PageUp/Down always remain outer conversation actions; local keyboard navigation must be focusable/discoverable.
- Thinking spinner local Timer/LoadingIndicator: active when unit running and widget visible on active Screen; stop on complete/finish/suspend/unmount/reduced-motion. Expose `set_running(bool)` and optional reduced-motion config from the TUI, no animation tick UiAction.
- Each inner viewport keeps own following flag; new content doesn't reattach user-detached inner or outer view. Disclosure collapse resizes via saved visible anchor rather than jumping to tail.

- [ ] **Step 1 — RED input/stream tests.** `test_tool_viewport_scrolls_without_outer_motion`, `test_inner_edge_wheel_propagates`, `test_stream_keeps_detached_inner_and_outer_offsets`, `test_long_message_scroll_only_after_height_limit`, `test_complete_thinking_stops_timer`, `test_live_thinking_rebuilt_still_running`, `test_suspend_stops_animation_not_session`, `test_reduced_motion_preserves_status_without_timer`. For preview growth/expand/collapse assert Composer text/selection/focus intact. Test 300/1000 turns remain bounded in mounted widgets, not total source memory.
- [ ] **Step 2 — Run RED.** Run execution_panel/message_cards/follow tests; expected missing local scrolling/status restoration.
- [ ] **Step 3 — Implement bounded local viewports.** Keep one scroller per region and route events according to movement capability. Stop/restart timer by real completion and visibility, not quiet-token timeout. Folded Thinking caches text without parsing Markdown. Width changes remeasure heights, not stale estimates.
- [ ] **Step 4 — Verify.** Run current CJK/Markdown incremental append/selection tests, bounded window tests, new scroll tests and full suite. Inspect one running and one completed execution view with expanded Thinking at normal and short widths; ensure stopped animation does not look active.

## Task 5: Slash Suggestions as a Composer-local Panel

**Files**
- Extend `domain/commands.py`; create `widgets/chat/command_menu.py`, `styles/widgets/command_menu.tcss` and matching tests.
- Modify `widgets/chat/{input_dock,composer}.py`, viewmodel command availability only as required; `screens/chat/screen.py` forwards accepted command through existing submit intent.
- Tests: `test/domain/test_commands.py`, `test/widgets/chat/{test_command_menu,test_composer,test_input_dock}.py`, `test/screens/chat/test_commands.py`.

**Interfaces**
- `CommandDefinition.usage: str = ""`, rendered as name when empty. `CommandRegistry.match(query: str) -> tuple[CommandDefinition, ...]`: case-insensitive command-name subsequence, stable registration order; empty query returns three actual commands.
- `CommandMenu.set_candidates(items, selected=0)`, `move(delta)`, `selection -> CommandDefinition | None`; emits `Chosen(definition)` on click/accept. No RPC, command execution or invented additional commands.
- InputDock manages suggestion eligibility and precedence; existing Composer retains editing/selection/paste semantics. When qualifying command-name completion is open, Up/Down choose, Tab completes, Enter submits selected command via the same PendingMessage path once; newline or active IME composition bypasses acceptance. Escape suppresses until next edit.
- Menu eligible for single-line draft, leading slash after whitespace, cursor at end, still in name token. Once args are present Enter validates the actual typed draft and does not overwrite it with a candidate. Images/unknown/malformed arguments retain complete original draft on rejection.
- Normal 5 candidate rows, short 2, but actual default list only three commands. Pending queue is not repurposed as a menu.

- [ ] **Step 1 — RED domain/key tests.** Assert match order for `""`, `"RE"`, `"rs"`, `"zz"` and usage fallback. Add `test_enter_executes_selected_candidate_once`, `test_tab_only_completes`, `test_escape_restores_editor_arrow_keys`, `test_multiline_or_argument_draft_not_hijacked`, `test_ime_composition_enter_not_command_accept`, `test_busy_resume_keeps_draft`, `test_command_attachment_rejection_keeps_markers`, `test_mouse_choice_does_not_double_submit`. Assert menu bounds shrink transcript without replacing Composer or moving detached anchor.
- [ ] **Step 2 — Run RED.** Run commands/menu/input_dock files; expected missing matching/candidate flow.
- [ ] **Step 3 — Implement local interaction.** Use explicit event stop/prevent-default only for keys consumed by menu. Respect Textual terminal key capabilities: where IME composition is not exposed, avoid treating uncommitted printable batches as an accept event and document native-terminal manual verification. Keep Shift+Enter/Ctrl+J and platform paste behavior. Busy commands still rejected in business layer even if menu advertises them.
- [ ] **Step 4 — Verify.** Run all editor/paste/marker/command tests and full suite. Inspect open command panel at wide/narrow/short sizes and confirm footer metadata stays below it.

## Task 6: End-to-end Target Review, Docs and Skills Synchronization

**Files**
- Update `apps/tui/README.md`, feature `design.md` for explicitly documented implementation decisions, create implementation-derived `arch.md` when verified.
- Update `.agents/skills/textual-tui/SKILL.md`, its TCSS/UX/verification references to actual new paths, split styles, App-worker ownership and no legacy SVG suite. Do not edit MassGen global skill or install new tooling implicitly.
- Add/extend `test/screens/chat/{test_navigation,test_layout,test_replay}.py`, deterministic fixtures only as needed. No screenshot baselines recreated.

**Interfaces**
- Three target states reached by real widget input/runtime fixture, not prototype-only buttons or fake production numbers.
- Replay CLI under scripts keeps its documented parameters; fixtures remain public RuntimeUpdate records. Headless harness must explicitly submit before awaiting events and invoke normal shutdown.
- Final tree contains no old gateway_client/root replay/root transcript/root messages/flat chat Screen imports; clients live under client/gateway only until another real client exists.

- [ ] **Step 1 — Add remaining RED acceptance tests.** Real app: initial Hero → submit → stream tools/thinking → append correction → finish → slash menu → push secondary Screen during another stream → return to same draft/reading position. Assert no duplicate transcript, no hidden executing UI, one subscription, command menu not a Screen, and app exit cleans all resource owners.
- [ ] **Step 2 — Run new acceptance test and implement only uncovered integration wiring.** Do not fix unrelated Agent/TUI baseline failures or change backend contracts to fill metadata.
- [ ] **Step 3 — Full verification.** Run targeted acceptance files, `make test-tui`, compileall, `git diff --check`, semantic replay, headless real-app replay with submit/shutdown. Record actual failure identities and skipped platform checks.
- [ ] **Step 4 — Render and inspect.** Capture welcome/chat/command states at 120×40 and 100×32; cover 70×24, 58×24, 100×12 for clipping, menu budget and focus. Use temporary screenshots/manual review, not deleted baseline assertions. Check active/completed spinner, local vs outer scroll, rail, safe previews, correction order and light New messages placement against approved HTML. Native Windows/macOS/Linux input checks only where environment exists; report unavailable environments.
- [ ] **Step 5 — Documentation.** Write arch.md from final code/tests, cross-link old 2026-10-26 record to this implementation, update commands and clearly list metadata/clipboard limitations. Update project skill so it does not require nonexistent snapshots/MassGen commands or old App worker ban. Remove stale prototype-only values from product code if any; retain clearly labelled HTML demo as design reference.
- [ ] **Step 6 — Independent review and handoff.** Review working diff for lifetime, same-Session stale results, resource cleanup, double projection, terminal input/scroll and real metadata. Fix verified important issues with regression tests. Report measured results and remaining limitations; leave commits/push for explicit user instruction.

## Self-review checklist for executor

- All design sections map to at least one task; future metadata endpoints/update checking/production extra pages remain excluded.
- Phase A owns workflow/status; Phase B does not create a second queue/transcript store.
- Command and scroll interactions are intentional product changes; do not claim behavior-preserving for those tasks.
- Unknown metadata and unsupported clipboard paths are visible, not fake values or silent successful states.
- Textual limitations must be tested in the installed version; browser demo behavior is not proof of native behavior.
