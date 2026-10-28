import asyncio

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.event_pipeline import (
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserMessage,
    CompleteThinking,
)
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.widgets.chat.execution_panel import (
    RunCard,
    ThinkingBlock,
    ToolRecords,
)
from apps.tui.test.services.chat.test_runtime import Client


def test_archived_thinking_accepts_late_complete_without_replacing_current(tmp_path):
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
            await apply(AppendThinkingDelta("t", 1, "partial old"))
            await apply(AppendThinkingDelta("t", 2, "latest"))
            await apply(CompleteThinking("t", 1, "complete old"))
            card = app.chat_screen.query_one(RunCard)
            archived = card.query_one(".thinking-history").query_one(ThinkingBlock)
            assert archived.markdown_text == "complete old"
            assert archived.completed
            assert (
                card.query_one(".current-thinking")
                .query_one(ThinkingBlock)
                .markdown_text
                == "latest"
            )
            app.request_shutdown()

    asyncio.run(run())


def test_run_record_height_grows_to_limit_and_default_follows_newest(tmp_path):
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
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "q"), AppendToolStarted("t", "a", "read", "{}"))
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            height = app.chat_screen.query_one(ToolRecords).region.height
            for i in range(18):
                app.chat_store.apply_actions(
                    (AppendToolStarted("t", str(i), "read", "{}"),)
                )
                await app.chat_screen.refresh_chat()
            await pilot.pause()
            records = app.chat_screen.query_one(ToolRecords)
            assert height == 1
            assert records.region.height == 8
            assert records.is_vertical_scroll_end
            assert records.show_vertical_scrollbar
            app.request_shutdown()

    asyncio.run(run())
