"""Execution groups follow assistant messages and grow up to a scroll limit."""

import asyncio

from textual.containers import VerticalScroll

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteAssistantMessage,
    CompleteThinking,
    FinishTurn,
)
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.widgets.chat.execution_panel import (
    RunCard,
    ThinkingBlock,
    ToolRecords,
)
from apps.tui.src.widgets.chat.message_cards import AssistantMessage
from apps.tui.test.services.chat.test_runtime import Client


def test_execution_records_split_after_each_assistant_not_after_correction():
    store = ChatStore()
    actions = (
        AppendUserMessage("t", "q"),
        AppendThinkingDelta("t", 1, "old"),
        CompleteThinking("t", 1, "old"),
        AppendToolStarted("t", "a", "read", "{}", 1),
        CompleteAssistantMessage("t", "first answer", 1),
        AppendUserCorrection("t", "more", 2),
        AppendThinkingDelta("t", 2, "latest"),
        AppendToolStarted("t", "b", "read", "{}", 2),
        AppendAssistantDelta("t", "full intermediate output", 2),
        FinishTurn("t", "completed"),
    )
    store.apply_actions(actions)
    cards = [u for u in store.conversation.units if u.kind == "run_card"]
    assert len(cards) == 2
    assert [[c.call_id for c in u.children if c.kind == "tool"] for u in cards] == [
        ["a"], ["b"],
    ]
    assert [u.segment for u in cards] == [0, 1]
    assert [u.text for u in store.conversation.units if u.kind == "assistant"] == [
        "first answer",
        "full intermediate output",
    ]
    assert not any(
        c.kind == "progress" for u in store.conversation.units for c in u.children
    )


def test_latest_thinking_below_fixed_records_old_thinking_archived_and_rebuild_same(
    tmp_path,
):
    async def run():
        client = Client()

        async def factory(s, c):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(None),
        )
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()

            async def apply(action):
                app.chat_store.apply_actions((action,))
                await app.chat_screen.refresh_chat()

            await apply(AppendUserMessage("t", "q"))
            await apply(AppendThinkingDelta("t", 1, "old thinking"))
            await apply(CompleteThinking("t", 1, "old thinking"))
            await apply(AppendToolStarted("t", "a", "read", "{}", 1))
            card = app.chat_screen.query_one(RunCard)
            records = card.query_one(ToolRecords)
            await pilot.pause()
            fixed_height = records.region.height
            await apply(AppendThinkingDelta("t", 2, "new thinking"))
            await pilot.pause()
            current = card.query_one(".current-thinking").query_one(ThinkingBlock)
            assert current.step == 2 and current.markdown_text == "new thinking"
            archived = records.query_one(ThinkingBlock)
            assert archived.step == 1 and archived.markdown_text == "old thinking"
            assert records.region.height == fixed_height + 1
            assert current.region.y >= records.region.bottom
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            card = app.chat_screen.query_one(RunCard)
            assert (
                card.query_one(".current-thinking").query_one(ThinkingBlock).step == 2
            )
            assert card.query_one(ToolRecords).query_one(ThinkingBlock).step == 1
            app.request_shutdown()

    asyncio.run(run())


def test_assistant_full_text_has_no_local_scroller_or_height_cap(tmp_path):
    async def run():
        client = Client()

        async def factory(s, c):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(None),
        )
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            text = "\n\n".join(f"paragraph {i}" for i in range(40))
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "q"), CompleteAssistantMessage("t", text))
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            assistant = app.chat_screen.query_one(AssistantMessage)
            assert not list(assistant.query(VerticalScroll))
            assert assistant.region.height > 12
            assert assistant.markdown_text == text
            app.request_shutdown()

    asyncio.run(run())
