"""Real-App coverage of message-scoped, lazily displayed execution records."""

import asyncio

import pytest

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserMessage,
    CompleteAssistantMessage,
    CompleteThinking,
    FinishTurn,
    UpdateToolCompleted,
)
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.execution_panel import (
    RunCard,
    ThinkingBlock,
    ToolBlock,
    ToolRecords,
)
from apps.tui.src.widgets.chat.message_cards import AssistantMessage
from apps.tui.test.services.chat.test_runtime import Client


def app_for(tmp_path):
    client = Client()

    async def factory(s, c):
        return client

    return IcarusTextualApp(
        TuiConfig(tmp_path), client_factory=factory, attachments=AttachmentsService(None)
    )


def test_each_assistant_owns_preceding_records_live_and_after_rebuild(tmp_path):
    async def run():
        app = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()

            async def apply(*actions):
                app.chat_store.apply_actions(actions)
                await app.chat_screen.refresh_chat()
                assert not app.chat_screen._view_failed

            await apply(AppendUserMessage("t", "q"))
            await apply(AppendToolStarted("t", "a", "read", "{}", 1))
            await apply(CompleteAssistantMessage("t", "first", 1))
            # Rebuild a sealed group before the next tool starts.
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            await apply(AppendThinkingDelta("t", 2, "second reasoning"))
            await apply(AppendToolStarted("t", "b", "search", "{}", 2))
            await apply(AppendAssistantDelta("t", "second", 2))
            await apply(CompleteAssistantMessage("t", "second", 2))
            await apply(UpdateToolCompleted("t", "a", "read", True, step=1))
            await apply(CompleteThinking("t", 2, "second reasoning complete"))
            await apply(UpdateToolCompleted("t", "b", "search", True, step=2))
            await apply(CompleteAssistantMessage("t", "plain answer", 3))
            await apply(FinishTurn("t", "completed"))

            def check():
                view = app.chat_screen.query_one(ConversationView)
                assert [
                    type(w).__name__ for w in view.children
                    if isinstance(w, (RunCard, AssistantMessage))
                ] == ["RunCard", "AssistantMessage", "RunCard", "AssistantMessage", "AssistantMessage"]
                cards = list(view.query(RunCard))
                assert [[w.call_id for w in card.query(ToolBlock)] for card in cards] == [["a"], ["b"]]
                assert [w.state_text for w in view.query(ToolBlock)] == ["completed", "completed"]
                assert cards[1].query_one(ThinkingBlock).markdown_text == "second reasoning complete"
                assert [w.markdown_text for w in view.query(AssistantMessage)] == ["first", "second", "plain answer"]

            check()
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            check()
            app.request_shutdown()

    asyncio.run(run())


def test_thinking_without_tools_never_shows_empty_execution_frame(tmp_path):
    async def run():
        app = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            for action in (
                AppendUserMessage("t", "q"),
                AppendThinkingDelta("t", 1, "thinking"),
                CompleteThinking("t", 1, "thinking"),
                CompleteAssistantMessage("t", "answer", 1),
                FinishTurn("t", "completed"),
            ):
                app.chat_store.apply_actions((action,))
                await app.chat_screen.refresh_chat()
                await pilot.pause()
                assert not [w for w in app.chat_screen.query(".execution-records-frame") if w.display]
            assert app.chat_screen.query_one(ThinkingBlock).markdown_text == "thinking"
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            assert not [w for w in app.chat_screen.query(".execution-records-frame") if w.display]
            app.request_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("size,limit", [((120, 40), 8), ((70, 24), 8), ((100, 12), 3)])
def test_records_grow_then_scroll_and_details_use_same_limit(tmp_path, size, limit):
    async def run():
        app = app_for(tmp_path)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()

            async def add(index):
                app.chat_store.apply_actions((AppendToolStarted("t", str(index), "read", '{}'),))
                await app.chat_screen.refresh_chat()
                await pilot.pause()
                return app.chat_screen.query_one(ToolRecords)

            app.chat_store.apply_actions((AppendUserMessage("t", "q"),))
            await app.chat_screen.refresh_chat()
            records = await add(0)
            tool = app.chat_screen.query_one(ToolBlock)
            assert records.region.height == 1
            tool.set_expanded(True)
            await pilot.pause()
            assert records.region.height == 3
            assert not records.show_vertical_scrollbar
            tool.set_expanded(False)
            await pilot.pause()
            assert records.region.height == 1
            assert not records.show_vertical_scrollbar
            for i in range(1, limit + 3):
                records = await add(i)
                assert records.region.height == min(i + 1, limit)
                assert records.show_vertical_scrollbar == (i + 1 > limit)
            assert records.is_vertical_scroll_end
            records.following = False
            records.scroll_home(animate=False, immediate=True)
            await pilot.pause()
            await add(99)
            assert records.scroll_y == 0
            tool = app.chat_screen.query_one(ToolBlock)
            tool.set_expanded(True)
            await pilot.pause()
            assert records.region.height == limit
            assert records.show_vertical_scrollbar
            app.request_shutdown()

    asyncio.run(run())
