import asyncio
from apps.tui.test.services.chat.test_runtime import Client
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.command_menu import CommandMenu


def test_mouse_command_candidate_selects_the_clicked_row(tmp_path):
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
            await pilot.press("/")
            await pilot.pause()
            chosen = []
            original = app.chat_session.submit

            def record(submission):
                chosen.append(submission.text)
                return original(submission)

            app.chat_session.submit = record
            menu = app.chat_screen.query_one(CommandMenu)
            await pilot.click(CommandMenu, offset=(5, 2))
            await pilot.pause()
            await pilot.pause()
            assert chosen == ["/clear"]
            app.request_shutdown()

    asyncio.run(run())
