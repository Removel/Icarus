"""Application-internal scrolling conversation projection."""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.containers import VerticalScroll

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

    def __init__(self, workspace_path: str | Path, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self._run_cards: dict[str, RunCard] = {}
        self._assistant_candidates: dict[
            tuple[str, int], AssistantMessage | AssistantProgressBlock
        ] = {}
        self._candidate_completed: set[tuple[str, int]] = set()
        self._completed_thinking: set[tuple[str, int]] = set()
        self._thinking: dict[tuple[str, int], ThinkingBlock] = {}
        self._tools: dict[tuple[str, str], ToolBlock] = {}
        self._anchor_pending = True
        self._restoring_history = False

    async def on_mount(self) -> None:
        await self.mount(WelcomeMessage(self.workspace_path))

    async def reset(self) -> None:
        """Replace the current Session projection with an empty one."""

        await self._finish_all_streams()
        self._run_cards.clear()
        self._assistant_candidates.clear()
        self._candidate_completed.clear()
        self._completed_thinking.clear()
        self._thinking.clear()
        self._tools.clear()
        self._restoring_history = False
        self.display = True
        self.anchor(False)
        self._anchor_pending = True
        await self.remove_children()
        await self.mount(WelcomeMessage(self.workspace_path))
        self.scroll_home(animate=False, immediate=True)

    async def append_user_message(self, text: str) -> None:
        await self.mount(UserMessage(text))
        if not self._restoring_history:
            self._activate_anchor_after_layout()

    def begin_history_restore(self) -> None:
        self._restoring_history = True
        self.display = False

    def finish_history_restore(self) -> None:
        self._restoring_history = False
        self.display = True
        self.resume_follow()

    async def apply_action(self, action: UiAction) -> bool:
        if isinstance(action, AppendUserMessage):
            await self.append_user_message(action.text)
        elif isinstance(action, AppendAssistantDelta):
            if (action.task_id, action.step) in self._candidate_completed:
                return True
            await self._advance_step(action.task_id, action.step)
            assistant = await self._ensure_assistant_candidate(
                action.task_id, action.step
            )
            await assistant.append_delta(action.text)
        elif isinstance(action, CompleteAssistantMessage):
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
            card = await self._ensure_run_card(action.task_id)
            await card.mount(
                UserCorrectionBlock(
                    action.text, action.applied_before_step
                )
            )
        elif isinstance(action, AppendToolStarted):
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
            await self.mount(ErrorMessage(action.error_type, action.message))
        elif isinstance(action, FinishTurn):
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
                expanded=True,
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

    def page_up(self) -> None:
        self.scroll_page_up(animate=False)

    def page_down(self) -> None:
        self.scroll_page_down(
            animate=False,
            on_complete=self._restore_follow_if_at_end,
        )

    def resume_follow(self) -> None:
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

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._restore_follow_if_at_end)
