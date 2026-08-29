"""Application-internal scrolling conversation projection."""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.containers import VerticalScroll

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
    AppendUserMessage,
    FinishTurn,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    ToolMessage,
    TurnStatusMessage,
    UserMessage,
    WelcomeMessage,
)


class ConversationView(VerticalScroll):
    """Render only conversation-target UiActions."""

    def __init__(self, workspace_path: str | Path, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self._active_assistant: AssistantMessage | None = None
        self._tools: dict[str, ToolMessage] = {}
        self._anchor_pending = True
        self._restoring_history = False

    async def on_mount(self) -> None:
        await self.mount(WelcomeMessage(self.workspace_path))

    async def append_user_message(self, text: str) -> None:
        await self._finish_assistant_segment()
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
            assistant = await self._ensure_assistant_segment()
            await assistant.append_delta(action.text)
        elif isinstance(action, AppendToolStarted):
            await self._finish_assistant_segment()
            tool = ToolMessage(
                call_id=action.call_id,
                tool_name=action.tool_name,
                arguments_json=action.arguments_json,
            )
            self._tools[action.call_id] = tool
            await self.mount(tool)
        elif isinstance(action, UpdateToolCompleted):
            await self._finish_assistant_segment()
            tool = self._tools.get(action.call_id)
            if tool is None:
                tool = ToolMessage(
                    call_id=action.call_id,
                    tool_name=action.tool_name,
                )
                self._tools[action.call_id] = tool
                await self.mount(tool)
            tool.complete(success=action.success, error=action.error)
        elif isinstance(action, AppendError):
            await self._finish_assistant_segment()
            await self.mount(ErrorMessage(action.error_type, action.message))
        elif isinstance(action, FinishTurn):
            await self._finish_assistant_segment()
            if action.status == "interrupted":
                for tool in self._tools.values():
                    tool.interrupt()
            if action.status in {"failed", "cancelled", "interrupted"}:
                await self.mount(TurnStatusMessage(action.status))
            self._tools.clear()
        else:
            return False

        if not self._restoring_history:
            self._activate_anchor_after_layout()
        return True

    async def _ensure_assistant_segment(self) -> AssistantMessage:
        assistant = self._active_assistant
        if assistant is not None:
            return assistant
        assistant = AssistantMessage()
        self._active_assistant = assistant
        await self.mount(assistant)
        return assistant

    async def _finish_assistant_segment(self) -> None:
        assistant = self._active_assistant
        if assistant is None:
            return
        self._active_assistant = None
        await assistant.finish()

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

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self.has_focus:
            event.stop()
            return
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self.has_focus:
            event.stop()
            return
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._restore_follow_if_at_end)
