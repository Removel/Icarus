import asyncio
from apps.tui.test.services.chat.test_runtime import Client
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.configs.config import TuiConfig


def test_slash_panel_keeps_composer_and_executes_selected_command(tmp_path):
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
            await pilot.pause()
            composer = app.chat_screen.query_one(PersistentComposer)
            await pilot.press("/")
            await pilot.pause()
            menu = app.chat_screen.query_one("#command-menu")
            assert menu.display
            assert app.chat_screen.query_one(PersistentComposer) is composer
            await pilot.press("escape")
            await pilot.pause()
            assert not menu.display
            assert composer.text == "/"
            app.request_shutdown(return_code=0)

    asyncio.run(run())
