"""Isolated spike for Textual 8.2.8 cross-message selection semantics.

This is a throwaway validation (design §5.1 / §9.1). Its pass/fail outcome
decides whether Task 4 can rely on native Screen selection or needs stable
endpoints (<message identity, text offset> mapping). Keep independent from
the production test suites; conclusions are recorded in the spec directory.
"""

import asyncio

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.geometry import Offset

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendUserMessage,
    CompleteAssistantMessage,
)
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.conversation import ConversationView


class SpikeConversationView(ConversationView):
    def __init__(self, workspace, **kwargs):
        self.test_store = ChatStore()
        super().__init__(workspace, projection=self.test_store.conversation, **kwargs)
        self._test_applying = False

    async def apply_action(self, action):
        self.test_store.apply_actions((action,))
        self._test_applying = True
        try:
            return await super().apply_action(action)
        finally:
            self._test_applying = False

    async def append_user_message(self, text, *, task_id=""):
        if not self._test_applying:
            self.test_store.apply_actions((AppendUserMessage(task_id, text),))
        await super().append_user_message(text, task_id=task_id)


class SpikeApp(App):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield SpikeConversationView(self.workspace, id="conversation")
            yield PersistentComposer(id="composer")


def _mouse_down(app, widget, offset=None, button=1):
    offset = offset or Offset(1, 1)
    x = widget.region.x + offset.x
    y = widget.region.y + offset.y
    app.screen._forward_event(
        events.MouseDown(
            None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=0,
            button=button,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
        )
    )


def _mouse_move(app, widget, offset):
    x = widget.region.x + offset.x
    y = widget.region.y + offset.y
    app.screen._forward_event(
        events.MouseMove(
            None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=0,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
        )
    )


def _mouse_up(app, widget, offset=None, button=1):
    offset = offset or Offset(1, 1)
    x = widget.region.x + offset.x
    y = widget.region.y + offset.y
    app.screen._forward_event(
        events.MouseUp(
            None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=0,
            button=button,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
        )
    )


def _selected_text(app) -> str:
    return app.screen.get_selected_text()


def test_spike_cross_message_drag_select(tmp_path):
    """Drag from a user message across into an assistant message."""

    async def run():
        app = SpikeApp(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            await view.apply_action(AppendUserMessage("t1", "第一个问题"))
            await view.apply_action(
                CompleteAssistantMessage("t1", "assistant answer text")
            )
            await pilot.pause()
            user = view.query_one("UserMessage")
            assistant = view.query_one("AssistantMessage")
            _mouse_down(app, user)
            _mouse_move(app, assistant, Offset(4, 1))
            _mouse_up(app, assistant, Offset(4, 1))
            await pilot.pause()
            return _selected_text(app)

    text = asyncio.run(run())
    # Reading-order concatenation of the two message bodies.
    assert "第一个问题" in text
    assert "assistant answer" in text
    assert "❯" not in text
    assert "✦" not in text


def test_spike_cjk_emoji_selection_character_complete(tmp_path):
    """Selecting across CJK and emoji must yield whole characters, no U+FFFD."""

    async def run():
        app = SpikeApp(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            await view.apply_action(
                CompleteAssistantMessage("t1", "中文😀测试ABC尾字")
            )
            await pilot.pause()
            assistant = view.query_one("AssistantMessage")
            _mouse_down(app, assistant, Offset(1, 1))
            _mouse_move(app, assistant, Offset(20, 1))
            _mouse_up(app, assistant, Offset(20, 1))
            await pilot.pause()
            return _selected_text(app)

    text = asyncio.run(run())
    assert "�" not in text
    assert "中文" in text
    assert "😀" in text


def test_spike_streaming_append_keeps_selection(tmp_path):
    """A selection made before a streaming append must survive and not grow."""

    async def run():
        app = SpikeApp(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            await view.apply_action(AppendUserMessage("t1", "select me"))
            await view.apply_action(AppendAssistantDelta("t1", "stream base"))
            await pilot.pause()
            user = view.query_one("UserMessage")
            _mouse_down(app, user)
            _mouse_move(app, user, Offset(6, 1))
            _mouse_up(app, user, Offset(6, 1))
            await pilot.pause()
            before = _selected_text(app)
            await view.apply_action(AppendAssistantDelta("t1", " more tail"))
            await pilot.pause()
            after = _selected_text(app)
            return before, after

    before, after = asyncio.run(run())
    assert before.strip() != ""
    assert before == after


def test_spike_markdown_tail_replace_extraction(tmp_path):
    """After the streamed tail block is replaced by re-parsing, extraction
    from the same selection must return the new text (not crash or stale)."""

    async def run():
        app = SpikeApp(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            await view.apply_action(AppendUserMessage("t1", "anchor start"))
            await view.apply_action(AppendAssistantDelta("t1", "before"))
            await pilot.pause()
            user = view.query_one("UserMessage")
            assistant = view.query_one("AssistantMessage")
            _mouse_down(app, user)
            _mouse_move(app, assistant, Offset(3, 1))
            _mouse_up(app, assistant, Offset(3, 1))
            await pilot.pause()
            before = _selected_text(app)
            await view.apply_action(
                CompleteAssistantMessage("t1", "replaced tail content")
            )
            await pilot.pause()
            after = _selected_text(app)
            return before, after

    before, after = asyncio.run(run())
    assert before.strip() != ""
    # Semantic endpoint must resolve to the replaced text, not empty/stale.
    assert after.strip() != ""


def test_spike_page_up_window_switch_selection_survives(tmp_path):
    """page_up across the 24-turn window boundary: the selection's origin
    text must still be extractable after the window re-mounts widgets."""

    async def run():
        app = SpikeApp(tmp_path)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            for index in range(30):
                await view.apply_action(
                    AppendUserMessage(f"task-{index}", f"question {index}")
                )
                await view.apply_action(
                    CompleteAssistantMessage(f"task-{index}", f"answer {index}")
                )
            await pilot.pause()
            # Select into the earliest mounted turn.
            first_user = next(
                widget
                for widget in view.query("UserMessage")
                if "question 6" in str(getattr(widget, "message_text", ""))
                or "question 6" in str(getattr(widget, "_text", ""))
            )
            _mouse_down(app, first_user)
            _mouse_move(app, first_user, Offset(6, 1))
            _mouse_up(app, first_user, Offset(6, 1))
            await pilot.pause()
            before = _selected_text(app)
            view.page_up()
            await pilot.pause()
            await pilot.pause()
            after = _selected_text(app)
            return before, after

    before, after = asyncio.run(run())
    assert before.strip() != ""
    # Selection origin must survive window switch (semantic endpoint).
    assert after.strip() != ""


def test_spike_drag_from_link_widget_does_not_open(tmp_path):
    """Starting a drag selection on a Markdown link must not fire LinkClicked."""

    opened = []

    async def run():
        app = SpikeApp(tmp_path)
        app.open_url = lambda url: opened.append(url)
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(SpikeConversationView)
            await view.apply_action(
                AppendUserMessage("t1", "see [the docs](https://example.com/docs)")
            )
            await view.apply_action(
                CompleteAssistantMessage("t1", "and a [link](https://example.com/x)")
            )
            await pilot.pause()
            user = view.query_one("UserMessage")
            # Down on the link, move away, up: must not trigger the link.
            _mouse_down(app, user)
            assistant = view.query_one("AssistantMessage")
            _mouse_move(app, assistant, Offset(5, 1))
            _mouse_up(app, assistant, Offset(5, 1))
            await pilot.pause()
            return _selected_text(app)

    asyncio.run(run())
    assert opened == []
