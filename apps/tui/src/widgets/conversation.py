"""Application-internal scrolling conversation projection."""

from __future__ import annotations

from pathlib import Path

from textual.containers import VerticalScroll

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
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

    async def on_mount(self) -> None:
        await self.mount(WelcomeMessage(self.workspace_path))

    async def append_user_message(self, text: str) -> None:
        await self._finish_assistant_segment()
        await self.mount(UserMessage(text))
        self._scroll_to_latest()

    async def apply_action(self, action: UiAction) -> bool:
        if isinstance(action, AppendAssistantDelta):
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
            if action.status == "failed":
                await self.mount(TurnStatusMessage(action.status))
            self._tools.clear()
        else:
            return False

        self._scroll_to_latest()
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

    def _scroll_to_latest(self) -> None:
        self.call_after_refresh(
            self.scroll_end,
            animate=False,
            immediate=True,
            x_axis=False,
        )
