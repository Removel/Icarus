import asyncio

from textual.app import App, ComposeResult

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendToolStarted,
    FinishTurn,
    SetRuntimeStatus,
    UpdateToolCompleted,
)
from apps.tui.src.widgets.conversation import ConversationView
from apps.tui.src.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    ToolMessage,
    TurnStatusMessage,
    UserMessage,
    WelcomeMessage,
)


class ConversationTestApp(App):
    def __init__(self, workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield ConversationView(self.workspace, id="conversation")


def test_conversation分割文本工具文本并更新工具状态(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.append_user_message("hello")
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="before")
            )
            await view.apply_action(
                AppendToolStarted(
                    task_id="task-1",
                    call_id="call-1",
                    tool_name="read",
                    arguments_json='{"path":"你好.md"}',
                )
            )
            await view.apply_action(
                UpdateToolCompleted(
                    task_id="task-1",
                    call_id="call-1",
                    tool_name="read",
                    success=True,
                )
            )
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="after")
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="completed")
            )
            await pilot.pause()
            assistants = list(view.query(AssistantMessage))
            tool = view.query_one(ToolMessage)
            return (
                len(view.query(WelcomeMessage)),
                len(view.query(UserMessage)),
                [assistant.markdown_text for assistant in assistants],
                tool.success,
                len(view.query(TurnStatusMessage)),
            )

    welcome_count, user_count, assistant_texts, tool_success, status_count = (
        asyncio.run(run())
    )

    assert welcome_count == 1
    assert user_count == 1
    assert assistant_texts == ["before", "after"]
    assert tool_success is True
    assert status_count == 0


def test_conversation对缺失start的失败工具降级并显示错误终态(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                UpdateToolCompleted(
                    task_id="task-1",
                    call_id="call-missing",
                    tool_name="bash",
                    success=False,
                    error="exit code 1",
                )
            )
            await view.apply_action(
                AppendError(
                    task_id="task-1",
                    error_type="RuntimeError",
                    message="agent failed",
                )
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="failed")
            )
            handled = await view.apply_action(
                SetRuntimeStatus(
                    task_id="task-1",
                    status="running",
                    text="running",
                )
            )
            await pilot.pause()
            tool = view.query_one(ToolMessage)
            return (
                tool.success,
                tool.error,
                len(view.query(ErrorMessage)),
                len(view.query(TurnStatusMessage)),
                handled,
            )

    tool_success, tool_error, error_count, status_count, handled = (
        asyncio.run(run())
    )

    assert tool_success is False
    assert tool_error == "exit code 1"
    assert error_count == 1
    assert status_count == 1
    assert handled is False
