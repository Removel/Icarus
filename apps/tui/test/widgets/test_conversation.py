import asyncio

from rich.cells import cell_len
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.geometry import Offset, Region
from textual.widgets import Markdown

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteThinking,
    CompleteAssistantMessage,
    FinishTurn,
    SetRuntimeStatus,
    UpdateToolCompleted,
)
from apps.tui.src.widgets.composer import PersistentComposer
from apps.tui.src.widgets.conversation import ConversationView
from apps.tui.src.widgets.messages import (
    AssistantMessage,
    AssistantProgressBlock,
    ErrorMessage,
    ICARUS_LOGO,
    RunCard,
    ThinkingBlock,
    ToolBlock,
    TurnStatusMessage,
    UserMessage,
    UserCorrectionBlock,
    WelcomeMessage,
    StreamingMarkdown,
    render_icarus_logo,
)


def test_icarus_logo保持九行且渲染源稿渐变():
    lines = ICARUS_LOGO.splitlines()

    assert len(lines) == 9
    assert all(line.strip() for line in lines)
    assert max(len(line) for line in lines) == 79
    assert set(ICARUS_LOGO) <= {"█", "▓", "▒", "░", " ", "\n"}

    rendered = render_icarus_logo()
    assert rendered.plain == ICARUS_LOGO
    assert rendered.spans[0].style == "rgb(184,183,190)"
    assert rendered.spans[-1].style == "rgb(217,119,131)"


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


class ConversationPointerTestApp(App):
    CSS = """
    #shell { height: 1fr; }
    #conversation { height: 1fr; }
    #composer { height: 2; }
    .message { height: auto; margin-bottom: 1; }
    """

    def __init__(self, workspace) -> None:
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield ConversationView(self.workspace, id="conversation")
            yield PersistentComposer(id="composer")


def dispatch_mouse_scroll(app, widget, event_type) -> None:
    x = widget.region.x + 1
    y = widget.region.y + 1
    app.screen._forward_event(
        event_type(
            None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=(-1 if event_type is events.MouseScrollUp else 1),
            button=0,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
        )
    )


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
                AppendAssistantDelta(
                    task_id="task-1", text="after", step=2
                )
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="completed")
            )
            await pilot.pause()
            assistants = list(view.query(AssistantProgressBlock))
            tool = view.query_one(ToolBlock)
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


def test_conversation完整消息校准流式文本且不重复(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="部分")
            )
            await view.apply_action(
                CompleteAssistantMessage(
                    task_id="task-1", text="部分完整"
                )
            )
            await view.apply_action(
                CompleteAssistantMessage(
                    task_id="task-1", text="部分完整"
                )
            )
            await pilot.pause()
            return view.query_one(AssistantMessage).markdown_text

    assert asyncio.run(run()) == "部分完整"


def test_conversation完成时保留每个完整assistant消息(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta("task-1", "checking", step=1)
            )
            await view.apply_action(
                CompleteAssistantMessage("task-1", "checking", step=1)
            )
            await view.apply_action(
                AppendToolStarted("task-1", "call", "read", "{}", step=1)
            )
            await view.apply_action(
                AppendAssistantDelta("task-1", "final", step=2)
            )
            await view.apply_action(
                CompleteAssistantMessage("task-1", "final answer", step=2)
            )
            await view.apply_action(FinishTurn("task-1", "completed"))
            await pilot.pause()
            return (
                [
                    item.markdown_text
                    for item in view.query(AssistantProgressBlock)
                ],
                [item.markdown_text for item in view.query(AssistantMessage)],
                len(view.query(RunCard)),
            )

    progress, final, cards = asyncio.run(run())
    assert progress == []
    assert final == ["checking", "final answer"]
    assert cards == 1


def test_conversation未完成文本在tool开始后降级为中间过程(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta("task-1", "checking", step=1)
            )
            live_candidate = view.query_one(AssistantMessage)
            await view.apply_action(
                AppendToolStarted(
                    "task-1", "call", "read", "{}", step=1
                )
            )
            await pilot.pause()
            progress = view.query_one(AssistantProgressBlock)
            return (
                live_candidate.markdown_text,
                live_candidate.parent,
                progress.markdown_text,
                progress.parent is view.query_one(RunCard),
                len(view.query(AssistantMessage)),
            )

    original_text, original_parent, progress_text, in_card, final_count = (
        asyncio.run(run())
    )

    assert original_text == "checking"
    assert original_parent is None
    assert progress_text == "checking"
    assert in_card is True
    assert final_count == 0


def test_conversation完整中间消息在后续tool开始后保持对话样式(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                CompleteThinking("task-1", 1, "inspect", partial=False)
            )
            await view.apply_action(
                AppendAssistantDelta(
                    "task-1",
                    "This is useful context before I inspect the repo.",
                    step=1,
                )
            )
            live_message = view.query_one(AssistantMessage)
            await view.apply_action(
                CompleteAssistantMessage(
                    "task-1",
                    "This is useful context before I inspect the repo.",
                    step=1,
                )
            )
            await view.apply_action(
                AppendToolStarted(
                    "task-1", "call", "bash", "{}", step=1
                )
            )
            await pilot.pause()
            cards = list(view.query(RunCard))
            direct_children = [
                type(child).__name__ for child in view.children
            ]
            return (
                view.query_one(AssistantMessage) is live_message,
                live_message.markdown_text,
                live_message.parent is view,
                len(view.query(AssistantProgressBlock)),
                len(cards),
                direct_children,
                view.query_one(ToolBlock).parent is cards[1],
            )

    (
        same_message,
        text,
        at_top_level,
        progress_count,
        card_count,
        order,
        tool_in_second_card,
    ) = asyncio.run(run())

    assert same_message is True
    assert text == "This is useful context before I inspect the repo."
    assert at_top_level is True
    assert progress_count == 0
    assert card_count == 2
    assert order == [
        "WelcomeMessage",
        "RunCard",
        "AssistantMessage",
        "RunCard",
    ]
    assert tool_in_second_card is True


def test_conversation工具循环后最终回答从首个delta起保持对话样式(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                CompleteThinking("task-1", 1, "inspect", partial=False)
            )
            await view.apply_action(
                AppendToolStarted("task-1", "call", "read", "{}", step=1)
            )
            await view.apply_action(
                UpdateToolCompleted(
                    "task-1", "call", "read", True, step=1
                )
            )
            await view.apply_action(
                AppendAssistantDelta("task-1", "final", step=2)
            )
            await pilot.pause()
            live_final = view.query_one(AssistantMessage)
            during_stream = (
                live_final.markdown_text,
                len(view.query(AssistantProgressBlock)),
                live_final.parent is view,
            )

            await view.apply_action(
                CompleteAssistantMessage(
                    "task-1", "final answer", step=2
                )
            )
            same_after_message = view.query_one(AssistantMessage) is live_final
            await view.apply_action(FinishTurn("task-1", "completed"))
            await pilot.pause()
            return (
                during_stream,
                same_after_message,
                view.query_one(AssistantMessage) is live_final,
                live_final.markdown_text,
            )

    during_stream, same_after_message, same_after_finish, text = asyncio.run(
        run()
    )

    assert during_stream == ("final", 0, True)
    assert same_after_message is True
    assert same_after_finish is True
    assert text == "final answer"


def test_conversation纯最终回答完成时移除空run_card(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                CompleteAssistantMessage("task-1", "answer", step=1)
            )
            await view.apply_action(FinishTurn("task-1", "completed"))
            await pilot.pause()
            return (
                len(view.query(RunCard)),
                view.query_one(AssistantMessage).markdown_text,
            )

    assert asyncio.run(run()) == (0, "answer")


def test_conversation思考流对账后始终默认展开并忽略迟到delta(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendThinkingDelta("task-1", 1, "partial")
            )
            first = view.query_one(ThinkingBlock)
            expanded_while_live = first.expanded
            await view.apply_action(
                CompleteThinking("task-1", 1, "complete", partial=False)
            )
            await view.apply_action(
                AppendThinkingDelta("task-1", 1, " late")
            )
            await view.apply_action(
                AppendThinkingDelta("task-1", 2, "next")
            )
            await view.apply_action(
                AppendToolStarted(
                    "task-1", "call-1", "read", "{}", step=2
                )
            )
            blocks = list(view.query(ThinkingBlock))
            await view.apply_action(FinishTurn("task-1", "completed"))
            await pilot.pause()
            return (
                expanded_while_live,
                [(item.markdown_text, item.expanded) for item in blocks],
            )

    expanded, blocks = asyncio.run(run())
    assert expanded is True
    assert blocks == [("complete", True), ("next", True)]


def test_conversation历史thinking默认展开且可键盘折叠(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            await view.apply_action(
                CompleteThinking(
                    "task-1", 1, "restored", historical=True
                )
            )
            view.finish_history_restore()
            thinking = view.query_one(ThinkingBlock)
            initially_expanded = thinking.expanded
            thinking.query_one(".thinking-summary").focus()
            await pilot.press("enter")
            await pilot.pause()
            return (
                thinking.markdown_text,
                initially_expanded,
                thinking.expanded,
            )

    assert asyncio.run(run()) == ("restored", True, False)


def test_conversation完整thinking对账保留用户手动折叠状态(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendThinkingDelta("task-1", 1, "partial")
            )
            thinking = view.query_one(ThinkingBlock)
            thinking.set_expanded(False)
            await view.apply_action(
                CompleteThinking("task-1", 1, "complete", partial=False)
            )
            await pilot.pause()
            return thinking.markdown_text, thinking.expanded

    assert asyncio.run(run()) == ("complete", False)


def test_conversation工具预览和追加内容位于同一run_card(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendUserCorrection("task-1", "check errors", 2)
            )
            await view.apply_action(
                UpdateToolCompleted(
                    "task-1",
                    "call",
                    "bash",
                    False,
                    "exit code 1",
                    output_preview={"stdout": "hello"},
                    preview_truncated=True,
                    full_result_available=True,
                )
            )
            tool = view.query_one(ToolBlock)
            tool.set_expanded(True)
            await pilot.pause()
            return (
                len(view.query(RunCard)),
                view.query_one(UserCorrectionBlock).correction_text,
                str(tool.query_one(".tool-details").render()),
                tool.state_text,
            )

    cards, correction, details, state = asyncio.run(run())
    assert cards == 1
    assert correction == "check errors"
    assert '"stdout": "hello"' in details
    assert "Full result remains on the Agent side" in details
    assert state == "failed"


def test_conversation_reset清空session投影并恢复欢迎内容(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.append_user_message("old message")
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="partial")
            )
            await view.apply_action(
                AppendToolStarted(
                    task_id="task-1",
                    call_id="call-1",
                    tool_name="read",
                    arguments_json="{}",
                )
            )

            await view.reset()
            await pilot.pause()
            return (
                len(view.query(WelcomeMessage)),
                len(view.query(UserMessage)),
                len(view.query(AssistantProgressBlock)),
                len(view.query(ToolBlock)),
                view._assistant_candidates,
                view._tools,
                view._restoring_history,
            )

    result = asyncio.run(run())
    assert result == (1, 0, 0, 0, {}, {}, False)


def test_streaming_markdown替换后旧节点不会触发鼠标选择崩溃(
    monkeypatch, tmp_path
):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="`")
            )
            markdown = app.query_one(StreamingMarkdown)
            await pilot.pause()
            old_block = markdown.children[0]

            await view.apply_action(
                AppendAssistantDelta(
                    task_id="task-1", text="``\ncode\n```"
                )
            )
            await pilot.pause()
            current_block = markdown.children[0]
            replaced = old_block is not current_block
            if replaced:
                monkeypatch.setattr(
                    app.screen,
                    "get_widget_and_offset_at",
                    lambda x, y: (old_block, Offset(0, 0)),
                )
                app.screen._forward_event(
                    events.MouseDown(
                        None,
                        x=1,
                        y=1,
                        delta_x=0,
                        delta_y=0,
                        button=1,
                        shift=False,
                        meta=False,
                        ctrl=False,
                    )
                )
            return replaced, old_block.allow_select, current_block.allow_select

    replaced, old_allow_select, current_allow_select = asyncio.run(run())

    assert replaced is True
    assert old_allow_select is False
    assert current_allow_select is True


def test_streaming_markdown后续delta不再全量update(monkeypatch, tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            first = "# Heading\n\nFirst paragraph.\n\n"
            second = "- first item\n"
            third = "- second item\n"
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=first)
            )
            markdown = app.query_one(StreamingMarkdown)

            def reject_full_update(_markdown):
                raise AssertionError("streaming delta used full Markdown.update")

            monkeypatch.setattr(markdown, "update", reject_full_update)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=second)
            )
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=third)
            )
            await view.apply_action(
                CompleteAssistantMessage(
                    task_id="task-1", text=first + second + third
                )
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="completed")
            )
            await pilot.pause()
            return markdown.source

    rendered_source = asyncio.run(run())

    expected = "# Heading\n\nFirst paragraph.\n\n- first item\n- second item\n"
    assert rendered_source == expected


def test_streaming_markdown只写入新fragment并在finish停止stream(
    monkeypatch, tmp_path
):
    class RecordingStream:
        def __init__(self) -> None:
            self.fragments = []
            self.stop_count = 0

        async def write(self, fragment):
            self.fragments.append(fragment)

        async def stop(self):
            self.stop_count += 1

    async def run():
        stream = RecordingStream()
        monkeypatch.setattr(
            Markdown, "get_stream", lambda markdown: stream
        )
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="first")
            )
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=" second")
            )
            assistant = view.query_one(AssistantMessage)
            await view.apply_action(
                CompleteAssistantMessage(
                    task_id="task-1", text="first second"
                )
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="completed")
            )
            await assistant.finish()
            await pilot.pause()
            return (
                stream.fragments,
                stream.stop_count,
                assistant.markdown_text,
                assistant._markdown_stream,
            )

    fragments, stop_count, markdown_text, active_stream = asyncio.run(run())

    assert fragments == ["first", " second"]
    assert stop_count == 1
    assert markdown_text == "first second"
    assert active_stream is None


def test_streaming_markdown中英文混排按终端cell折行(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 20)) as pilot:
            view = app.query_one(ConversationView)
            text = (
                "博客正文：我用 RSS 抓了 18 篇全文存成文件，但真正进我眼里的是"
                "我摘出来读的那些片段，加上归档页、分类页的列表输出。"
                "这次一共处理了 9 万 4 千字。"
            )
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=text)
            )
            await pilot.pause()
            markdown = app.query_one(StreamingMarkdown)
            paragraph = app.query_one("MarkdownParagraph")
            lines = [
                line.text.rstrip()
                for line in paragraph.render_lines(
                    Region(0, 0, paragraph.region.width, paragraph.region.height)
                )
            ]
            return (
                markdown.source,
                paragraph.render().plain,
                paragraph.region.width,
                paragraph.styles.text_wrap,
                lines,
            )

    source, rendered_text, width, text_wrap, lines = asyncio.run(run())

    assert source == rendered_text
    assert text_wrap == "wrap"
    assert all(cell_len(line) <= width for line in lines)
    assert "".join(lines).replace(" ", "") == rendered_text.replace(
        " ", ""
    )
    assert all(not line.endswith("18") for line in lines)
    assert any("18 篇" in line for line in lines)
    assert all(not line.endswith(("9", "4")) for line in lines)
    assert any("9 万" in line and "4 千" in line for line in lines)


def test_streaming_markdown纯英文仍按单词换行(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(40, 20)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta(
                    task_id="task-1",
                    text="The conversation remains readable on a narrow terminal.",
                )
            )
            await pilot.pause()
            paragraph = app.query_one("MarkdownParagraph")
            lines = [
                line.text.rstrip()
                for line in paragraph.render_lines(
                    Region(
                        0,
                        0,
                        paragraph.region.width,
                        paragraph.region.height,
                    )
                )
            ]
            return (
                paragraph.styles.text_wrap,
                paragraph.region.width,
                lines,
            )

    text_wrap, width, lines = asyncio.run(run())

    assert text_wrap == "wrap"
    assert lines == [
        "The conversation remains readable",
        "on a narrow terminal.",
    ]
    assert all(cell_len(line) <= width for line in lines)


def test_streaming_markdown保留已稳定的前部block(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            first = "# Stable heading\n\nStable paragraph.\n\n"
            tail = "## Growing tail\n\nTail content.\n"
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=first)
            )
            await pilot.pause()
            markdown = app.query_one(StreamingMarkdown)
            stable_block = markdown.children[0]

            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text=tail)
            )
            await view.apply_action(
                CompleteAssistantMessage(
                    task_id="task-1", text=first + tail
                )
            )
            await view.apply_action(
                FinishTurn(task_id="task-1", status="completed")
            )
            await pilot.pause()
            return (
                stable_block,
                markdown.children[0],
                markdown.source,
                stable_block.allow_select,
            )

    stable_block, current_first_block, source, allow_select = asyncio.run(run())

    assert current_first_block is stable_block
    assert allow_select is True
    assert source == (
        "# Stable heading\n\nStable paragraph.\n\n"
        "## Growing tail\n\nTail content.\n"
    )


def test_conversation_reset会停止仍在输出的markdown_stream(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(
                AppendAssistantDelta(task_id="task-1", text="partial")
            )
            assistant = view.query_one(AssistantMessage)
            stream = assistant._markdown_stream

            await view.reset()
            await pilot.pause()
            return stream._task, assistant._markdown_stream

    stream_task, active_stream = asyncio.run(run())

    assert stream_task is None
    assert active_stream is None


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
            tool = view.query_one(ToolBlock)
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


def test_conversation恢复用户部分回复和未完成工具为interrupted(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test() as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage("task-1", "hello"))
            await view.apply_action(
                AppendAssistantDelta("task-1", "partial answer")
            )
            await view.apply_action(
                AppendToolStarted(
                    "task-1", "call-1", "read", '{"path":"a"}'
                )
            )
            await view.apply_action(FinishTurn("task-1", "interrupted"))
            await pilot.pause()
            tool = view.query_one(ToolBlock)
            return (
                len(view.query(UserMessage)),
                view.query_one(AssistantProgressBlock).markdown_text,
                tool.state_text,
                str(view.query_one(TurnStatusMessage).render()),
            )

    user_count, assistant, tool_state, status = asyncio.run(run())
    assert user_count == 1
    assert assistant == "partial answer"
    assert tool_state == "interrupted"
    assert status == "Task interrupted"


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
            max_before_growth = view.max_scroll_y
            for index in range(8):
                await view.apply_action(
                    AppendAssistantDelta(
                        task_id="task-1",
                        text=(
                            f"\n\nstreamed paragraph {index} with enough "
                            "content to grow the layout"
                        ),
                    )
                )
            await pilot.pause()
            detached_after = view.scroll_y
            max_after = view.max_scroll_y

            view.resume_follow()
            await pilot.pause()
            resumed = (view.scroll_y, view.max_scroll_y)
            await view.apply_action(
                AppendAssistantDelta(
                    task_id="task-1",
                    text="\n\nmore content that grows the layout again",
                )
            )
            await pilot.pause()
            followed_after_delta = (view.scroll_y, view.max_scroll_y)
            return (
                following,
                detached_before,
                max_before_growth,
                detached_after,
                max_after,
                resumed,
                followed_after_delta,
            )

    (
        following,
        detached_before,
        max_before_growth,
        detached_after,
        max_after,
        resumed,
        followed_after_delta,
    ) = asyncio.run(run())

    assert following[0] == following[1]
    assert detached_before < max_after
    assert max_after > max_before_growth
    assert detached_after == detached_before
    assert resumed[0] == resumed[1]
    assert followed_after_delta[0] == followed_after_delta[1]


def test_composer聚焦时conversation区域滚轮可以脱离底部(tmp_path):
    async def run():
        app = ConversationPointerTestApp(tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            view = app.query_one(ConversationView)
            for index in range(30):
                await view.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()

            composer = app.query_one(PersistentComposer)
            composer.load_text("draft line one\ndraft line two")
            composer.move_cursor((0, 5))
            composer.focus()
            view.resume_follow()
            await pilot.pause()
            before_composer = (
                composer.text,
                composer.selection,
                composer.cursor_location,
                app.focused,
            )
            before_scroll = view.scroll_y
            dispatch_mouse_scroll(app, view, events.MouseScrollUp)
            await pilot.pause()
            detached_scroll = view.scroll_y
            max_before_growth = view.max_scroll_y
            for index in range(8):
                await view.apply_action(
                    AppendAssistantDelta(
                        task_id="task-1",
                        text=(
                            f"\n\nstreamed mouse paragraph {index} with enough "
                            "content to grow the layout"
                        ),
                    )
                )
            await pilot.pause()
            return (
                before_scroll,
                detached_scroll,
                view.scroll_y,
                max_before_growth,
                view.max_scroll_y,
                before_composer,
                (
                    composer.text,
                    composer.selection,
                    composer.cursor_location,
                    app.focused,
                ),
            )

    (
        before_scroll,
        detached_scroll,
        after_growth_scroll,
        max_before_growth,
        max_after_growth,
        before_composer,
        after_composer,
    ) = asyncio.run(run())

    assert isinstance(before_composer[3], PersistentComposer)
    assert before_scroll == max_before_growth
    assert detached_scroll < before_scroll
    assert after_growth_scroll == detached_scroll
    assert max_after_growth > max_before_growth
    assert after_composer == before_composer


def test_composer区域滚轮不改变conversation位置(tmp_path):
    async def run():
        app = ConversationPointerTestApp(tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            view = app.query_one(ConversationView)
            for index in range(30):
                await view.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()
            view.page_up()
            await pilot.pause()

            composer = app.query_one(PersistentComposer)
            composer.load_text("draft")
            composer.focus()
            before_scroll = view.scroll_y
            dispatch_mouse_scroll(app, composer, events.MouseScrollUp)
            await pilot.pause()
            return before_scroll, view.scroll_y, app.focused

    before_scroll, after_scroll, focused = asyncio.run(run())

    assert after_scroll == before_scroll
    assert isinstance(focused, PersistentComposer)


def test_conversation滚轮回到底部后恢复流式跟随(tmp_path):
    async def run():
        app = ConversationPointerTestApp(tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            view = app.query_one(ConversationView)
            for index in range(30):
                await view.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()
            view.resume_follow()
            await pilot.pause()

            dispatch_mouse_scroll(app, view, events.MouseScrollUp)
            await pilot.pause()
            detached = view.scroll_y
            while not view.is_vertical_scroll_end:
                dispatch_mouse_scroll(app, view, events.MouseScrollDown)
                await pilot.pause()
            at_end = (view.scroll_y, view.max_scroll_y)

            await view.apply_action(
                AppendAssistantDelta(
                    task_id="task-1",
                    text="\n\nnew content after returning to the live bottom",
                )
            )
            await pilot.pause()
            return detached, at_end, (view.scroll_y, view.max_scroll_y)

    detached, at_end, after_growth = asyncio.run(run())

    assert detached < at_end[1]
    assert at_end[0] == at_end[1]
    assert after_growth[0] == after_growth[1]
    assert after_growth[1] > at_end[1]


def test_scrollbar拖动后流式增长保持阅读位置(tmp_path):
    async def run():
        app = ConversationPointerTestApp(tmp_path)
        async with app.run_test(size=(58, 16)) as pilot:
            view = app.query_one(ConversationView)
            for index in range(40):
                await view.append_user_message(
                    f"history {index} with enough content to overflow"
                )
            await pilot.pause()
            view.resume_follow()
            await pilot.pause()

            scrollbar = view.vertical_scrollbar
            grabbed = await pilot.mouse_down(
                scrollbar, offset=(0, max(0, scrollbar.region.height - 2))
            )
            thumb_grabbed = scrollbar.grabbed is not None
            await pilot.hover(scrollbar, offset=(0, 1))
            await pilot.mouse_up(scrollbar, offset=(0, 1))
            await pilot.pause()
            detached_scroll = view.scroll_y
            max_before_growth = view.max_scroll_y

            for index in range(8):
                await view.apply_action(
                    AppendAssistantDelta(
                        task_id="task-1",
                        text=(
                            f"\n\nstreamed scrollbar paragraph {index} with "
                            "enough content to grow the layout"
                        ),
                    )
                )
            await pilot.pause()
            return (
                grabbed,
                thumb_grabbed,
                detached_scroll,
                view.scroll_y,
                max_before_growth,
                view.max_scroll_y,
            )

    grabbed, thumb_grabbed, detached, after_growth, max_before, max_after = (
        asyncio.run(run())
    )

    assert grabbed is True
    assert thumb_grabbed is True
    assert detached < max_before
    assert after_growth == detached
    assert max_after > max_before
