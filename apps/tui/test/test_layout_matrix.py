"""Native target-layout acceptance at the five documented terminal sizes."""

import asyncio
import pytest
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.test.services.chat.test_runtime import Client


@pytest.mark.parametrize("size", [(120, 40), (100, 32), (70, 24), (58, 24), (100, 12)])
def test_open_command_panel_preserves_three_transcript_rows_and_metadata(
    tmp_path, size
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
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            screen = app.chat_screen
            transcript = screen.query_one("#conversation").region
            composer = screen.query_one("#composer-shell").region
            menu = screen.query_one("#command-menu")
            meta = screen.query_one("#session-metadata").region
            assert transcript.height >= 3
            assert composer.bottom <= menu.region.y
            assert menu.region.bottom <= meta.y
            assert meta.bottom <= screen.region.bottom
            assert composer.right <= screen.region.right
            app.request_shutdown()

    asyncio.run(run())
