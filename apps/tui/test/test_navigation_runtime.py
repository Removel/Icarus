import asyncio
from pathlib import Path

from textual.screen import Screen

from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.test.services.chat.test_runtime import Client


def test_application_retains_composer_while_other_page_is_active(tmp_path):
    from apps.tui.src.app import IcarusTextualApp
    from apps.tui.src.widgets.chat.composer import PersistentComposer

    async def run():
        client = Client()

        async def factory(session_id, create):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(tmp_path),
        )
        async with app.run_test(size=(100, 32)) as pilot:
            for _ in range(20):
                await pilot.pause()
                if app.chat_session.client:
                    break
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text("retained draft")
            await app.push_screen(Screen())
            assert app.chat_session.client is client
            assert not client.closed
            await app.pop_screen()
            await pilot.pause()
            assert app.chat_screen.query_one(PersistentComposer) is composer
            assert composer.text == "retained draft"
            app.request_shutdown(return_code=0)
            await pilot.pause()

    asyncio.run(run())
