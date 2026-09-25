"""Application-internal scrolling conversation projection."""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.containers import VerticalScroll
from textual.message import Message

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    CompleteAssistantMessage,
    AppendError,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteThinking,
    FinishTurn,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.widgets.conversation_projection import (
    ConversationProjection,
    DisplayUnit,
)
from apps.tui.src.widgets.messages import (
    AssistantMessage,
    AssistantProgressBlock,
    ErrorMessage,
    RunCard,
    ThinkingBlock,
    ToolBlock,
    TurnStatusMessage,
    UserMessage,
    UserCorrectionBlock,
    WelcomeMessage,
    StreamingMarkdown,
)


class ConversationView(VerticalScroll):
    """Render only conversation-target UiActions."""

    class FollowChanged(Message):
        """The view started or stopped following the live tail."""

        def __init__(self, following: bool) -> None:
            self.following = following
            super().__init__()

    def __init__(self, workspace_path: str | Path, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.projection = ConversationProjection()
        self._window_start = 0
        self._window_end = 0
        self._window_widgets: list[tuple[int, object]] = []
        self._rendered_turn: int | None = None
        self._history_deferred = False
        self._detached_window = False
        self._window_loading = False
        self._reading_history = False
        self._live_tail_task: str | None = None
        self._live_tail_widgets: list[object] = []
        self._run_cards: dict[str, RunCard] = {}
        self._assistant_candidates: dict[
            tuple[str, int], AssistantMessage | AssistantProgressBlock
        ] = {}
        self._candidate_completed: set[tuple[str, int]] = set()
        self._completed_thinking: set[tuple[str, int]] = set()
        self._thinking: dict[tuple[str, int], ThinkingBlock] = {}
        self._tools: dict[tuple[str, str], ToolBlock] = {}
        self._visible_turns: list[tuple[int, list[object]]] = []
        self._anchor_pending = True
        self._restoring_history = False
        self._following = True

    @property
    def following_live_tail(self) -> bool:
        """Whether new output keeps the newest content in view on its own."""

        return not self._detached_window and not self._reading_history

    def _set_reading_history(self, reading: bool) -> None:
        if self._reading_history != reading:
            self._reading_history = reading
            self._announce_follow_state()

    def _set_detached_window(self, detached: bool) -> None:
        if self._detached_window != detached:
            self._detached_window = detached
            self._announce_follow_state()

    def _announce_follow_state(self) -> None:
        following = self.following_live_tail
        if following != self._following:
            self._following = following
            self.post_message(self.FollowChanged(following))

    async def on_mount(self) -> None:
        await self.mount(WelcomeMessage(self.workspace_path))

    async def reset(self) -> None:
        """Replace the current Session projection with an empty one."""

        await self._finish_all_streams()
        self.projection.reset()
        self._window_start = self._window_end = 0
        self._window_widgets.clear()
        self._rendered_turn = None
        self._history_deferred = False
        self._set_detached_window(False)
        self._window_loading = False
        self._set_reading_history(False)
        self._live_tail_task = None
        self._live_tail_widgets.clear()
        self._run_cards.clear()
        self._assistant_candidates.clear()
        self._candidate_completed.clear()
        self._completed_thinking.clear()
        self._thinking.clear()
        self._tools.clear()
        self._visible_turns.clear()
        self._restoring_history = False
        self.display = True
        self.anchor(False)
        self._anchor_pending = True
        await self.remove_children()
        await self.mount(WelcomeMessage(self.workspace_path))
        self.scroll_home(animate=False, immediate=True)

    async def append_user_message(self, text: str, *, task_id: str = "") -> None:
        self.projection.apply(AppendUserMessage(task_id, text))
        if self._detached_window:
            if self._live_tail_task and self._live_tail_task != task_id:
                await self._clear_live_tail()
            self._live_tail_task = task_id
            widget = UserMessage(text)
            await self.mount(widget)
            self._live_tail_widgets.append(widget)
            return
        if self.turn_count > 24 and len(self._visible_turns) >= 24:
            if self._reading_history:
                self._set_detached_window(True)
                return
            old_index, old_widgets = self._visible_turns.pop(0)
            detached = not self.is_vertical_scroll_end
            old_scroll = self.scroll_y
            self._save_disclosures(old_index, old_widgets)
            self._forget_widgets(old_widgets)
            for widget in old_widgets:
                if widget.is_mounted:
                    await widget.remove()
            self._window_start = old_index + 1
            if detached:
                self.anchor(False)
                self._set_detached_window(True)
                self.scroll_to(y=old_scroll, animate=False, immediate=True)
        widget = UserMessage(text)
        await self.mount(widget)
        index = self.turn_count - 1
        self._visible_turns.append((index, [widget]))
        self._window_end = self.turn_count
        self._rendered_turn = index
        if not self._restoring_history:
            self._activate_anchor_after_layout()

    async def _clear_live_tail(self) -> None:
        widgets = self._live_tail_widgets
        self._live_tail_widgets = []
        self._live_tail_task = None
        self._forget_widgets(widgets)
        for widget in widgets:
            if widget.is_mounted:
                await widget.remove()

    def _save_disclosures(self, index: int, widgets: list[object]) -> None:
        for widget in widgets:
            if not isinstance(widget, RunCard):
                continue
            for unit in self.projection.snapshot_for_turn(index):
                if unit.kind != "run_card":
                    continue
                blocks = [child for child in widget.children
                          if isinstance(child, (ThinkingBlock, ToolBlock))]
                for child in blocks:
                    for model in unit.children:
                        if (isinstance(child, ThinkingBlock)
                                and model.kind == "thinking"
                                and model.step == child.step):
                            model.expanded = child.expanded
                        elif (isinstance(child, ToolBlock)
                              and model.kind == "tool"
                              and model.call_id == child.call_id):
                            model.expanded = child.expanded


    def _forget_widgets(self, widgets: list[object]) -> None:
        """Remove references to evicted Textual subtrees; retain projection state."""
        top = set(widgets)
        self._window_widgets = [entry for entry in self._window_widgets
                                if entry[1] not in top]
        self._run_cards = {key: card for key, card in self._run_cards.items()
                           if not any(card is widget or card in widget.walk_children()
                                      for widget in top)}
        self._assistant_candidates = {
            key: candidate for key, candidate in self._assistant_candidates.items()
            if not any(candidate is widget or candidate in widget.walk_children()
                       for widget in top)
        }
        self._thinking = {
            key: thinking for key, thinking in self._thinking.items()
            if not any(thinking is widget or thinking in widget.walk_children()
                       for widget in top)
        }
        self._tools = {
            key: tool for key, tool in self._tools.items()
            if not any(tool is widget or tool in widget.walk_children()
                       for widget in top)
        }

    def begin_history_restore(self) -> None:
        self._restoring_history = True
        self._history_deferred = True
        self._window_start = 0
        self._window_end = 0
        self._rendered_turn = None
        self.display = False

    def finish_history_restore(self) -> None:
        self._restoring_history = False
        self.display = True
        self.run_worker(self._show_latest_window(), exclusive=True,
                        group="conversation-window")

    async def finish_history_restore_async(self) -> None:
        self._restoring_history = False
        await self._show_latest_window()
        self.display = True

    async def _show_latest_window(self) -> None:
        count = self.turn_count
        if self._history_deferred:
            if count:
                await self._render_window(max(0, count - 24), count)
                self._rendered_turn = count - 1
            else:
                await self._render_untagged_units()
        self._history_deferred = False
        self.resume_follow()

    async def _render_untagged_units(self) -> None:
        for unit in self.projection.units:
            await self._mount_unit(unit)

    @property
    def turn_count(self) -> int:
        return len(self.projection.turns)

    @property
    def current_turn_index(self) -> int | None:
        return self._rendered_turn

    @property
    def mounted_turn_range(self) -> tuple[int, int]:
        return self._window_start, self._window_end

    async def jump_to_turn(self, index: int) -> None:
        if not 0 <= index < self.turn_count:
            raise IndexError(index)
        if self._window_start <= index < self._window_end:
            self._rendered_turn = index
            self.anchor(False)
            user = next((widget for turn, group in self._visible_turns
                         if turn == index for widget in group
                         if isinstance(widget, UserMessage)), None)
            if user is not None:
                self.scroll_to_widget(user, animate=False, immediate=True)
            return
        start = min(max(0, index - 12), max(0, self.turn_count - 24))
        end = min(self.turn_count, start + 24)
        await self._render_window(start, end)
        self._rendered_turn = index
        self.anchor(False)
        user = next(
            (widget for turn, widget in self._window_widgets
             if turn == index and isinstance(widget, UserMessage)),
            None,
        )
        if user is not None:
            self.scroll_to_widget(user, animate=False, immediate=True)

    async def _render_window(self, start: int, end: int) -> None:
        for index, widgets in self._visible_turns:
            self._save_disclosures(index, widgets)
        await self._finish_all_streams()
        self._run_cards.clear()
        self._assistant_candidates.clear()
        self._candidate_completed.clear()
        self._completed_thinking.clear()
        self._thinking.clear()
        self._tools.clear()
        await self.remove_children()
        self._live_tail_widgets.clear()
        self._live_tail_task = None
        self._window_widgets.clear()
        self._visible_turns.clear()
        for index in range(start, end):
            turn_widgets = []
            for unit in self.projection.snapshot_for_turn(index):
                widget = await self._mount_unit(unit)
                self._window_widgets.append((index, widget))
                turn_widgets.append(widget)
            self._visible_turns.append((index, turn_widgets))
        self._window_start, self._window_end = start, end
        self._set_detached_window(end < self.turn_count)

    async def _mount_unit(self, unit: DisplayUnit):
        if unit.kind == "user":
            widget = UserMessage(unit.text)
            await self.mount(widget)
        elif unit.kind in {"assistant", "progress"}:
            widget = (
                AssistantMessage() if unit.kind == "assistant"
                else AssistantProgressBlock(unit.step)
            )
            await self.mount(widget)
            await widget.complete_text(unit.text)
            await widget.finish()
        elif unit.kind == "run_card":
            widget = RunCard(unit.task_id)
            await self.mount(widget)
            for child in unit.children:
                if child.kind == "thinking":
                    thinking = ThinkingBlock(step=child.step,
                                             expanded=child.expanded,
                                             historical=True)
                    await widget.mount(thinking)
                    await thinking.complete_text(child.text, partial=child.partial)
                elif child.kind == "tool":
                    tool = ToolBlock(call_id=child.call_id,
                                     tool_name=child.tool_name,
                                     arguments_json=child.arguments_json)
                    await widget.mount(tool)
                    if child.status in {"completed", "failed"}:
                        tool.complete(
                            success=child.status == "completed",
                            error=child.error,
                            output_preview=child.output_preview,
                            preview_truncated=child.preview_truncated,
                            full_result_available=child.full_result_available,
                            preview_error=child.preview_error,
                        )
                    elif child.status == "interrupted":
                        tool.interrupt()
                    tool.set_expanded(child.expanded)
                elif child.kind == "correction":
                    await widget.mount(UserCorrectionBlock(
                        child.text, child.applied_before_step or 0
                    ))
                elif child.kind == "progress":
                    progress = AssistantProgressBlock(child.step)
                    await widget.mount(progress)
                    await progress.complete_text(child.text)
                    await progress.finish()
        elif unit.kind == "error":
            widget = ErrorMessage(unit.status, unit.text)
            await self.mount(widget)
        elif unit.kind == "turn_status":
            widget = TurnStatusMessage(unit.status)
            await self.mount(widget)
        else:
            raise ValueError(f"Unknown display unit: {unit.kind}")
        return widget

    async def apply_action(self, action: UiAction) -> bool:
        if self._restoring_history:
            if isinstance(action, AppendUserMessage) and self.turn_count >= 23:
                self._history_deferred = True
            if self._history_deferred:
                return self.projection.apply(action)
        if self._detached_window and (
            self._live_tail_task != action.task_id or self._live_tail_task is None
        ) and not isinstance(action, AppendUserMessage):
            return self.projection.apply(action)
        if isinstance(action, AppendUserMessage):
            await self.append_user_message(action.text, task_id=action.task_id)
        elif isinstance(action, AppendAssistantDelta):
            self.projection.apply(action)
            if (action.task_id, action.step) in self._candidate_completed:
                return True
            await self._advance_step(action.task_id, action.step)
            assistant = await self._ensure_assistant_candidate(
                action.task_id, action.step
            )
            await assistant.append_delta(action.text)
        elif isinstance(action, CompleteAssistantMessage):
            self.projection.apply(action)
            key = (action.task_id, action.step)
            if key in self._candidate_completed:
                return True
            await self._advance_step(action.task_id, action.step)
            assistant = await self._ensure_assistant_candidate(
                action.task_id, action.step
            )
            await assistant.complete_text(action.text)
            await assistant.finish()
            self._candidate_completed.add(key)
            await self._close_run_card_segment(action.task_id)
        elif isinstance(action, AppendThinkingDelta):
            self.projection.apply(action)
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                await self._advance_step(action.task_id, action.step)
                thinking = await self._ensure_thinking(
                    action.task_id,
                    action.step,
                    historical=action.historical,
                )
                await thinking.append_delta(action.text)
        elif isinstance(action, CompleteThinking):
            self.projection.apply(action)
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                await self._advance_step(action.task_id, action.step)
                thinking = await self._ensure_thinking(
                    action.task_id,
                    action.step,
                    historical=action.historical,
                )
                await thinking.complete_text(
                    action.text, partial=action.partial
                )
                self._completed_thinking.add(key)
        elif isinstance(action, AppendUserCorrection):
            self.projection.apply(action)
            card = await self._ensure_run_card(action.task_id)
            await card.mount(
                UserCorrectionBlock(
                    action.text, action.applied_before_step
                )
            )
        elif isinstance(action, AppendToolStarted):
            self.projection.apply(action)
            await self._advance_step(action.task_id, action.step)
            await self._downgrade_candidate(action.task_id, action.step)
            card = await self._ensure_run_card(action.task_id)
            tool = ToolBlock(
                call_id=action.call_id,
                tool_name=action.tool_name,
                arguments_json=action.arguments_json,
            )
            self._tools[(action.task_id, action.call_id)] = tool
            await card.mount(tool)
        elif isinstance(action, UpdateToolCompleted):
            self.projection.apply(action)
            await self._advance_step(action.task_id, action.step)
            await self._downgrade_candidate(action.task_id, action.step)
            key = (action.task_id, action.call_id)
            tool = self._tools.get(key)
            if tool is None:
                card = await self._ensure_run_card(action.task_id)
                tool = ToolBlock(
                    call_id=action.call_id,
                    tool_name=action.tool_name,
                )
                self._tools[key] = tool
                await card.mount(tool)
            tool.complete(
                success=action.success,
                error=action.error,
                output_preview=action.output_preview,
                preview_truncated=action.preview_truncated,
                full_result_available=action.full_result_available,
                preview_error=action.preview_error,
            )
        elif isinstance(action, AppendError):
            self.projection.apply(action)
            await self.mount(ErrorMessage(action.error_type, action.message))
        elif isinstance(action, FinishTurn):
            self.projection.apply(action)
            if action.status == "interrupted":
                for (task_id, _), tool in self._tools.items():
                    if task_id == action.task_id:
                        tool.interrupt()
            await self._finish_task_candidates(action.task_id)
            if action.status in {"failed", "cancelled", "interrupted"}:
                await self.mount(TurnStatusMessage(action.status))
            await self._remove_empty_run_card(action.task_id)
        else:
            return False

        if self._detached_window and self._live_tail_task == action.task_id:
            recorded = set(self._live_tail_widgets)
            mounted = {existing for _, group in self._visible_turns
                       for existing in group}
            for widget in self.children:
                if widget not in recorded and widget not in mounted:
                    self._live_tail_widgets.append(widget)
        if self._visible_turns and not isinstance(action, AppendUserMessage) and not self._detached_window:
            current_index, widgets = self._visible_turns[-1]
            if self.projection.turn_index(action.task_id) == current_index:
                for widget in self.children:
                    if widget not in widgets and widget not in (w for _, group in self._visible_turns[:-1] for w in group):
                        widgets.append(widget)
        if not self._restoring_history:
            self._activate_anchor_after_layout()
        return True

    async def _ensure_run_card(self, task_id: str) -> RunCard:
        card = self._run_cards.get(task_id)
        if card is None:
            card = RunCard(task_id)
            self._run_cards[task_id] = card
            live_candidate = next(
                (
                    candidate
                    for (candidate_task, candidate_step), candidate in (
                        self._assistant_candidates.items()
                    )
                    if candidate_task == task_id
                    and isinstance(candidate, AssistantMessage)
                    and (candidate_task, candidate_step)
                    not in self._candidate_completed
                    and candidate.parent is self
                ),
                None,
            )
            if live_candidate is None:
                await self.mount(card)
            else:
                await self.mount(card, before=live_candidate)
        return card

    async def _ensure_assistant_candidate(
        self, task_id: str, step: int
    ) -> AssistantMessage | AssistantProgressBlock:
        key = (task_id, step)
        assistant = self._assistant_candidates.get(key)
        if assistant is None:
            assistant = AssistantMessage()
            self._assistant_candidates[key] = assistant
            card = self._run_cards.get(task_id)
            if card is None:
                await self.mount(assistant)
            else:
                await self.mount(assistant, after=card)
        return assistant

    async def _ensure_thinking(
        self, task_id: str, step: int, *, historical: bool
    ) -> ThinkingBlock:
        key = (task_id, step)
        thinking = self._thinking.get(key)
        if thinking is None:
            card = await self._ensure_run_card(task_id)
            thinking = ThinkingBlock(
                step=step,
                expanded=False,
                historical=historical,
            )
            self._thinking[key] = thinking
            await card.mount(thinking)
        return thinking

    async def _advance_step(self, task_id: str, step: int) -> None:
        previous_steps = {
            candidate_step
            for candidate_task, candidate_step in self._assistant_candidates
            if candidate_task == task_id and candidate_step < step
        }
        for previous_step in sorted(previous_steps):
            await self._downgrade_candidate(task_id, previous_step)

    async def _downgrade_candidate(self, task_id: str, step: int) -> None:
        key = (task_id, step)
        assistant = self._assistant_candidates.get(key)
        if assistant is None:
            return
        if key in self._candidate_completed:
            return
        if isinstance(assistant, AssistantProgressBlock):
            await assistant.finish()
            return

        text = assistant.markdown_text
        await assistant.finish()
        card = await self._ensure_run_card(task_id)
        await assistant.remove()
        progress = AssistantProgressBlock(step)
        self._assistant_candidates[key] = progress
        self._candidate_completed.discard(key)
        await card.mount(progress)
        await progress.complete_text(text)
        await progress.finish()

    async def _finish_task_candidates(self, task_id: str) -> None:
        keys = [
            key for key in self._assistant_candidates if key[0] == task_id
        ]
        for key in sorted(keys):
            if key in self._candidate_completed:
                candidate = self._assistant_candidates.get(key)
                if candidate is not None:
                    await candidate.finish()
            else:
                await self._downgrade_candidate(*key)
        for key in keys:
            self._assistant_candidates.pop(key, None)
            self._candidate_completed.discard(key)

    async def _close_run_card_segment(self, task_id: str) -> None:
        card = self._run_cards.pop(task_id, None)
        if card is not None and not self._run_card_is_meaningful(card):
            await card.remove()

    async def _remove_empty_run_card(self, task_id: str) -> None:
        card = self._run_cards.get(task_id)
        if card is None:
            return
        if not self._run_card_is_meaningful(card):
            self._run_cards.pop(task_id, None)
            await card.remove()

    @staticmethod
    def _run_card_is_meaningful(card: RunCard) -> bool:
        return any(
            isinstance(
                child,
                (
                    ThinkingBlock,
                    ToolBlock,
                    UserCorrectionBlock,
                    AssistantProgressBlock,
                ),
            )
            for child in card.children
        )

    async def _finish_all_streams(self) -> None:
        for assistant in self._assistant_candidates.values():
            await assistant.finish()
        for thinking in self._thinking.values():
            await thinking.finish()

    async def _resume_latest_window(self) -> None:
        start = max(0, self.turn_count - 24)
        await self._render_window(start, self.turn_count)
        self._rendered_turn = self.turn_count - 1
        self.resume_follow()

    def page_up(self) -> None:
        self._set_reading_history(True)
        if self._window_start > 0 and self.scroll_y <= self.size.height:
            self.run_worker(self._page_window(-8), exclusive=True,
                            group="conversation-window")
            return
        self.scroll_page_up(animate=False)

    def page_down(self) -> None:
        if self._window_end < self.turn_count and (
            self.max_scroll_y - self.scroll_y <= self.size.height
        ):
            self.run_worker(self._page_window(8), exclusive=True,
                            group="conversation-window")
            return
        self.scroll_page_down(
            animate=False,
            on_complete=self._restore_follow_if_at_end,
        )

    async def _page_window(self, delta: int) -> None:
        if self._window_loading:
            return
        self._window_loading = True
        try:
            start = max(0, min(self.turn_count - 24,
                               self._window_start + delta))
            if start == self._window_start:
                return
            top = self._top_visible_turn()
            await self._render_window(start, min(self.turn_count, start + 24))
            self.anchor(False)
            if top is not None:
                index, offset = top
                user = next((widget for n, widget in self._window_widgets
                             if n == index and isinstance(widget, UserMessage)), None)
                if user is not None:
                    self.scroll_to(y=max(0, user.region.y - self.region.y
                                         - offset + self.scroll_y),
                                   animate=False, immediate=True)
            self._rendered_turn = self._top_visible_turn()[0] if self._top_visible_turn() else start
        finally:
            self._window_loading = False

    def _top_visible_turn(self) -> tuple[int, int] | None:
        for index, widget in self._window_widgets:
            if isinstance(widget, UserMessage) and widget.region.bottom > self.region.y:
                return index, widget.region.y - self.region.y
        return None


    def resume_follow(self) -> None:
        self._set_reading_history(False)
        if self._detached_window and self.turn_count:
            self.run_worker(self._resume_latest_window(), exclusive=True,
                            group="conversation-window")
            return
        if self.max_scroll_y <= 0:
            self.anchor(False)
            self._anchor_pending = True
            self.scroll_home(animate=False, immediate=True)
            return
        self._anchor_pending = False
        self.anchor()
        self.scroll_end(
            animate=False,
            immediate=True,
            x_axis=False,
        )

    def action_page_up(self) -> None:
        self.page_up()

    def action_page_down(self) -> None:
        self.page_down()

    def action_scroll_down(self) -> None:
        self.scroll_down(
            animate=False,
            on_complete=self._restore_follow_if_at_end,
        )

    def action_scroll_end(self) -> None:
        self.resume_follow()

    def _restore_follow_if_at_end(self) -> None:
        if self.max_scroll_y <= 0:
            self._anchor_pending = True
        elif self.is_vertical_scroll_end:
            self._anchor_pending = False
            # Scrolling back to the tail is the reader returning to the newest
            # output, so the history-reading state ends with it.
            self._set_reading_history(False)
            self.anchor()

    def _activate_anchor_after_layout(self) -> None:
        if self._anchor_pending:
            self.call_after_refresh(self._activate_anchor_if_scrollable)

    def _activate_anchor_if_scrollable(self) -> None:
        if self._anchor_pending and self.max_scroll_y > 0:
            self._anchor_pending = False
            self.anchor()

    def on_streaming_markdown_content_appended(
        self, event: StreamingMarkdown.ContentAppended
    ) -> None:
        event.stop()
        if not self._restoring_history:
            self._activate_anchor_after_layout()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._window_start > 0 and self.scroll_y <= self.size.height:
            self._set_reading_history(True)
            self.run_worker(self._page_window(-8), exclusive=True,
                            group="conversation-window")
            event.stop()
            return
        self._set_reading_history(True)
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._window_end < self.turn_count and (
            self.max_scroll_y - self.scroll_y <= self.size.height
        ):
            self.run_worker(self._page_window(8), exclusive=True,
                            group="conversation-window")
            event.stop()
            return
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._restore_follow_if_at_end)
