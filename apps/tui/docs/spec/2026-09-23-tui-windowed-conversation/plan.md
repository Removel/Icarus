# TUI Windowed Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. No commit without explicit user authorization.

**Goal:** Make long TUI sessions usable with a bounded conversation Widget tree, scrollable user-input navigation and collapsed-by-default Thinking.

**Architecture:** Keep App / Gateway / UiAction unchanged. Store a chronological, view-owned display projection independent of mounted Textual Widgets; reconcile the visible turn window and a live tail. Put user-turn navigation in a separate Textual Widget and leave the current native conversation scrollbar and follow anchor intact.

**Tech Stack:** Python 3.12, Textual >=8.2.8,<9, pytest, pytest-textual-snapshot.

**Spec:** [arch.md](arch.md)

## Global Constraints

- Only `apps/tui` changes; no Agent/Gateway/Event protocol changes. Follow `AGENTS.md` and the per-feature `apps/tui/docs/spec/YYYY-MM-DD-*/{arch,plan}.md` layout.
- Preserve RunCard segmentation, `(task_id, step)` / `(task_id, call_id)` identities, sequence deduplication, queue semantics and native Textual scroll-follow.
- 16–24 user turns is an **initial test window**, not a measured performance target; keep raw historical text and ensure no historical Widget references survive eviction.
- Thinking defaults folded for both live and restored history. User-open state lasts through window eviction within the current Session.
- The rail shows at most 10 unnumbered dots at about 30px / 1.5 terminal lines center spacing, about 2 cells wide; hover reveals a compact single-line excerpt list for those 10 turns, with no title, range, count or older/newer copy.
- Do not branch on model vendor or add speculative shared packages. Functional pytest with native `assert` and tests mirroring the TUI source.
- Do not branch, commit, push or amend without an explicit request; prior generic skill commit instructions do not override the repository rule.

## Review Focus

1. Restored history containing complete Assistant followed by same-task Tool must still display Assistant before the next RunCard segment (Task 2 test).
2. Tool completion without start, including duplicate call IDs across tasks, must reconstruct each correct Tool state offscreen (Task 2 test).
3. Changing width while reading a manually expanded historical Thinking must preserve selected turn and remeasure, not reuse stale heights (Task 3 test).
4. Terminal with no hover or too few columns for a rail must retain keyboard access / usable composer and Conversation (Task 4 test).
5. Late complete record or live delta during detached history browsing must not replace the user's scroll location or silently lose text (Tasks 2–3 tests).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `apps/tui/src/widgets/messages.py` | Thinking disclosure lifecycle; store raw text while collapsed and render Markdown on demand. |
| `apps/tui/src/widgets/conversation.py` | Existing rendering and scroll controller; route actions into projection, reconcile mounted window, and handle jump/follow. |
| `apps/tui/src/widgets/conversation_projection.py` (new) | Ordered semantic display units + user-turn index; no Textual Widgets. |
| `apps/tui/src/widgets/turn_rail.py` (new) | Scrollable finite set of user-turn summaries, hover/click/key interaction, selection/jump message. |
| `apps/tui/src/app.py`, `apps/tui/src/styles.tcss`, `apps/tui/src/widgets/__init__.py` | Mount rail beside Conversation and route jump/new-message controls. |
| `apps/tui/test/widgets/test_conversation.py`, `test_conversation_projection.py`, `test_turn_rail.py`; `apps/tui/test/test_app.py`, `test_app_snapshots.py` | Focused data/widget/integration/snapshot and 20/300/1000-turn regression coverage. |

## Task 1: Collapsed Thinking Without Hidden Markdown Work

**Files:** Modify `apps/tui/src/widgets/messages.py:430-511`, `apps/tui/src/widgets/conversation.py:233-247`, `apps/tui/test/widgets/test_conversation.py:417-497`, affected snapshots in `apps/tui/test/__snapshots__/test_app_snapshots/`.

**Interfaces:** Consume current `ThinkingBlock(step, historical)` and `append_delta/complete_text/set_expanded/finish`; keep signatures unchanged. Produce default `expanded=False`, `markdown_text`, and a lazy body that renders from complete raw text when opened.

- [ ] **Step 1: Add failing regression.** Adapt existing assertions for live and historical Thinking to `False`; assert no `StreamingMarkdown` work on delta/complete while folded, then `set_expanded(True)` yields complete text and a second fold/reopen retains it. Preserve the existing keyboard toggle and complete-after-manual-selection tests. Example assertion:

```python
thinking = view.query_one(ThinkingBlock)
assert thinking.expanded is False
assert thinking.markdown_text == "complete"
assert thinking.query_one(StreamingMarkdown).display is False
thinking.set_expanded(True)
await pilot.pause()
assert thinking.query_one(StreamingMarkdown).source == "complete"
```

- [ ] **Step 2: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py -q -k thinking`; expect changed default/hidden-work assertions to fail.
- [ ] **Step 3: Implement:** set default false in `ThinkingBlock` and `_ensure_thinking`; while folded append only `_markdown_parts` (no stream); `complete_text` sets raw authoritative text and partial flag without `update`; on open schedule a single update from `markdown_text`, avoiding races with late delta; on fold finish any active stream, and ensure reopened body reflects the newest raw version. Keep `set_expanded` public and synchronous to match callers; perform async Markdown updates via Textual's mounted callback/worker and a generation token or equivalent serialized update. Preserve `finish` and unmount cancellation.
- [ ] **Step 4: Run focused tests and snapshot diff:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py -q`; update only impacted thinking snapshots after inspecting their SVG changes. Also test `pilot.press('enter')` with Composer focus intact.

## Task 2: Ordered View Projection and Virtualization Contract

**Files:** Create `apps/tui/src/widgets/conversation_projection.py`, `apps/tui/test/widgets/test_conversation_projection.py`; modify `apps/tui/src/widgets/conversation.py` and corresponding widget tests.

**Interfaces:** Consume `UiAction` from `apps/tui/src/event_pipeline/actions.py`. Produce `ConversationProjection.apply(action: UiAction) -> bool`, `.turns`, `.units`, `.reset()`, `.snapshot_for_turn(index: int)`; a `Turn` holds its originating `AppendUserMessage.task_id`, raw text and ordered unit IDs. Unit snapshots contain full text, type, step/call-id, status, disclosure state and chronological segment order; never hold Textual Widgets.

- [ ] **Step 1: Write failing pure tests** that apply `AppendUserMessage`, `CompleteAssistantMessage`, `AppendToolStarted`, `UpdateToolCompleted`, `AppendThinkingDelta`, `CompleteThinking`, `AppendUserCorrection` and `FinishTurn`; assert one navigation turn per `AppendUserMessage`, completion overrides delta, identical call_id in two tasks remains separate, complete Assistant remains before subsequent Tool card, and correction does not create a turn. Use concrete event/action constructors from `actions.py` (e.g. `AppendUserMessage("task-1", "question")`).
- [ ] **Step 2: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation_projection.py -q`; expect import failure.
- [ ] **Step 3: Implement pure projection** using ordered unit identifiers and typed data classes; port existing `_advance_step`, `_downgrade_candidate`, `_close_run_card_segment`, `_finish_task_candidates` transitions so the view no longer depends on historical widgets for state. Track active task IDs separately from prior segments; generate a display unit for errors/failed terminal states. Duplicate complete/late deltas stay idempotent. Do not add a second app-level history cache or replay Gateway events on remount.
- [ ] **Step 4: Wire ConversationView to read projection**, initially with all units mounted to prove semantic parity; add tests comparing visible ordering, interrupted Tool, missing Tool start, session reset and reconnect-style complete/delta overlap to current expected UI. Keep Action accepted/ignored contract and scroll-follow behavior unchanged.
- [ ] **Step 5: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py apps/tui/test/widgets/test_conversation_projection.py apps/tui/test/test_app.py -q`; expect pass before window limiting.

## Task 3: Bounded Mounted Window and Stable Reading Position

**Files:** Modify `apps/tui/src/widgets/conversation.py`, `apps/tui/src/styles.tcss`; test `apps/tui/test/widgets/test_conversation.py`, `apps/tui/test/test_app.py`.

**Interfaces:** Consume `ConversationProjection.turns/units/snapshot_for_turn`. Produce `ConversationView.jump_to_turn(index: int)`, `.current_turn_index`, `.turn_count`, `.mounted_turn_range`; keep existing `.apply_action`, `.reset`, `.begin_history_restore`, `.finish_history_restore`, `.page_up/down`, `.resume_follow` behavior.

- [ ] **Step 1: Add failing 300/1000-turn tests.** Apply actions in history-restore mode; assert projection retains all user turns, `len(view.query(UserMessage)) <= 24`, and jumping to turn 1 and back to 1000 shows exact text without materializing intervening turns. Assert active tail survives while the reader is detached and user-position remains unchanged after appended delta. Add Tool/Thinking content and completion cases so results survive eviction/rebuild.
- [ ] **Step 2: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py -q -k window`; expect boundedness/jump failure.
- [ ] **Step 3: Implement window reconciliation** in bounded batches, initially last 16–24 turns. Remove offscreen widgets and clear their references; cache measured geometry as needed, invalidate on width/disclosure changes; keep the nearest visible message ID and its viewport-relative offset across mount/remove. On target jump mount directly around requested turn and call `scroll_to_widget` after layout. Keep the live active tail mounted separately (at most active task content), preserving current Textual anchor semantics on follow. History restore builds data while hidden and mounts once at the end.
- [ ] **Step 4: Add edge/loading and resize tests.** Scroll to the upper/lower edge to request adjacent batch; verify no oscillation, reading anchor stays on same text; resize while expanded Thinking and verify remeasure; detach then complete Thinking/Assistant to verify no unexpected snap; reset/replace session clears widget refs and index.
- [ ] **Step 5: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_conversation.py apps/tui/test/test_app.py -q`; inspect any Changed Snapshot later, not by bulk `--snapshot-update`.

## Task 4: Scrollable User-Turn Rail and App Integration

**Files:** Create `apps/tui/src/widgets/turn_rail.py`, `apps/tui/test/widgets/test_turn_rail.py`; modify `apps/tui/src/widgets/__init__.py`, `apps/tui/src/app.py:271-279`, `apps/tui/src/styles.tcss:36-43,363-374`, `apps/tui/test/test_app.py`, `apps/tui/test/test_app_snapshots.py`.

**Interfaces:** Consume read-only user-turn index `(index, text)` from `ConversationView`. Produce `TurnRail.set_turns(turns: Sequence[str], current: int)`, `TurnRail.TurnSelected(index: int)`; App handles selection with `await conversation.jump_to_turn(index)`. Rail wheel changes its own 10-turn window only; no Gateway/UiAction parsing.

- [ ] **Step 1: Add failing rail tests** for 0/1/300/1000 input turns, at most 10 unnumbered circle markers with a short-terminal fallback, a hover-only compact excerpt list with no title/count/range/older-newer text, clipping of multiline/image text, pointer wheel with unchanged Conversation `scroll_y`, click target index, focused keyboard arrows/PageUp/PageDown/Home/End/Enter, and tiny width fallback keeping Composer available.
- [ ] **Step 2: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_turn_rail.py -q`; expect import failure.
- [ ] **Step 3: Implement rail as a lightweight Textual widget** with a slice of at most 10 turn indices and one dot per visible turn; no Canvas or per-all-turn widgets. Use actual event-local coordinates/geometry, stop handled wheel events, preserve pointer hit region and keyboard focus semantics. Default presentation is roughly two terminal cells wide, with dots centered about 1.5 terminal lines apart; the current dot is filled. Hover shows a narrow, compact single-line excerpt list with its first row aligned to the first visible dot; every excerpt begins with its dot and is truncated by terminal-cell width so CJK text cannot wrap the marker to another line. The list has no persistent title, counters, ranges, numbers or navigation copy. `TurnSelected` carries only an integer.
- [ ] **Step 4: Integrate in app** with a horizontal conversation/rail parent, adjust styles for narrow screens; index updates from ConversationView's turn changes rather than parsing RuntimeUpdate again. App handles rail selection/jump. Add a small new-output indicator tied to detached state; clicking it calls `resume_follow()` and clears pending count (increment on meaningful new message, not each delta). Ensure queue panel, Composer, status bar remain in original vertical order.
- [ ] **Step 5: Run:** `apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets/test_turn_rail.py apps/tui/test/test_app.py -q`; then inspect and update only changed snapshots, including narrow/empty cases.

## Task 5: Integration, Performance Evidence and Documentation

**Files:** Modify `apps/tui/test/test_app.py`, `apps/tui/test/test_app_snapshots.py`, affected snapshot SVGs, `apps/tui/README.md`, this `arch.md` only if implementation differs.

**Interfaces:** No new protocol. Scenario `20/300/1000` tests assert bounded *mounted* widget counts and deterministic restoration of first/middle/last turns.

- [ ] **Step 1: Add tests** for 300/1000-turn history/reconnect, explicit jump and return to latest, Thinking default folded and user-selected expansion after remount, active Tool/Assistant update while detached, image-only and long multiline user inputs, narrow terminal and keyboard-only access. Use snapshot `test_snapshot_*` for rail and folded thinking, inspect rendered SVG before changing tracked snapshots.
- [ ] **Step 2: Measure and record** initial history restoration, jump, and live update latency at 20/300/1000 turns along with mounted Widget count in test/benchmark output; compare to baseline where possible without inventing a fixed target. Verify one huge single-turn Markdown remains a known limitation.
- [ ] **Step 3: Run checks** in repository order:

```bash
apps/tui/.venv/bin/python -m pytest apps/tui/test/widgets -q
apps/tui/.venv/bin/python -m pytest apps/tui/test -q
make test-tui
apps/tui/.venv/bin/python -m compileall -q apps/tui/src apps/tui/test packages
git diff --check
```

- [ ] **Step 4: Update docs** describing the rail scroll/click/keyboard behavior, bounded-render contract, lazy Thinking, and known giant-single-turn limitation in `apps/tui/README.md`; align the spec if measured facts differ. Report failures and skipped checks explicitly; do not fix unrelated environment/baseline snapshot failures as part of the feature.

## Baseline (2026-09-23, before code changes)

After fast-forward-style rebase of `feat/tui` onto `origin/feature` (HEAD `2633c14`), TUI venv initially lacked pytest. Installed `apps/tui/requirements-dev.txt` without changing tracked files. Baseline non-snapshot suite: **188 passed**. `make test-tui` baseline fails 13 snapshot comparisons, including `test_snapshot_initial_welcome`, before feature code changes; retain the report and distinguish baseline mismatches from intended snapshot changes. Do not bulk rewrite snapshots to hide baseline discrepancies.
