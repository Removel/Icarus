import asyncio
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteThinking
from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock
from apps.tui.test.services.chat.test_runtime import Client


def test_manual_disclosure_survives_snapshot_reconcile(tmp_path):
    async def run():
        client = Client()

        async def factory(s, c):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(None),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "hello"), CompleteThinking("t", 1, "reasoning"))
            )
            await app.chat_screen.refresh_chat()
            thinking = app.chat_screen.query_one(ThinkingBlock)
            thinking.set_expanded(True)
            await pilot.pause()
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            assert app.chat_screen.query_one(ThinkingBlock).expanded
            app.request_shutdown()

    asyncio.run(run())
