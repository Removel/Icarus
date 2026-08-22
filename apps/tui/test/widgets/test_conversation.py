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
    ICARUS_LOGO,
    ToolMessage,
    TurnStatusMessage,
    UserMessage,
    WelcomeMessage,
)


def test_icarus_logo保持八行且包含完整品牌名():
    lines = ICARUS_LOGO.splitlines()

    assert len(lines) == 8
    assert all(line.strip() for line in lines)
    assert max(len(line) for line in lines) <= 72


class ConversationTestApp(App):
    CSS = """
    #conversation { height: 8; }
    .message { height: auto; margin-bottom: 1; }
    """

    def __init__(self, workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        yield ConversationView(self.workspace, id="conversation")


def test_conversation初始欢迎卡位于顶部且尚未启用底部anchor(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(50, 20)) as pilot:
            await pilot.pause()
            view = app.query_one(ConversationView)
            welcome = app.query_one(WelcomeMessage)
            return (
                view.scroll_y,
                welcome.region.y,
                view.content_region.y,
                view._anchored,
            )

    scroll_y, welcome_y, content_y, anchored = asyncio.run(run())

    assert scroll_y == 0
    assert welcome_y == content_y
    assert anchored is False


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


def test_conversation上滚后新输出保持阅读位置且恢复后继续跟随(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(50, 10)) as pilot:
            view = app.query_one(ConversationView)
            for index in range(30):
                await view.append_user_message(
                    f"message {index} with enough content to overflow"
                )
            await pilot.pause()
            following = (view.scroll_y, view.max_scroll_y)

            view.page_up()
            await pilot.pause()
            detached_before = view.scroll_y
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="new delta")
            )
            await pilot.pause()
            detached_after = view.scroll_y
            max_after = view.max_scroll_y

            view.resume_follow()
            await pilot.pause()
            resumed = (view.scroll_y, view.max_scroll_y)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="\nmore")
            )
            await pilot.pause()
            followed_after_delta = (view.scroll_y, view.max_scroll_y)
            return (
                following,
                detached_before,
                detached_after,
                max_after,
                resumed,
                followed_after_delta,
            )

    (
        following,
        detached_before,
        detached_after,
        max_after,
        resumed,
        followed_after_delta,
    ) = asyncio.run(run())

    assert following[0] == following[1]
    assert detached_before < max_after
    assert detached_after == detached_before
    assert resumed[0] == resumed[1]
    assert followed_after_delta[0] == followed_after_delta[1]
