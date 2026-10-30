import asyncio

import pytest
from textual.events import MouseDown

from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteThinking
from apps.tui.src.widgets.chat.execution_panel import DisclosureSummary, ThinkingBlock
from apps.tui.test.support import make_app, wait_ready
from apps.tui.test.services.chat.test_runtime import Client


@pytest.mark.parametrize("size", [(100, 32), (60, 12)])
def test_disclosure_click_and_keyboard_toggle_without_text_selection(size):
    async def run():
        app = make_app()
        async with app.run_test(size=size) as pilot:
            await wait_ready(pilot)
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "hello"), CompleteThinking("t", 1, "reasoning"))
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            thinking = app.chat_screen.query_one(ThinkingBlock)
            summary = thinking.query_one(DisclosureSummary)
            await pilot.click(summary, offset=(1, 0))
            await pilot.pause()
            assert thinking.expanded is True
            assert app.screen._select_state is None
            summary.focus()
            await pilot.press("enter")
            assert thinking.expanded is False
            await pilot.press("space")
            assert thinking.expanded is True
            app.request_shutdown()
    asyncio.run(run())


def test_detached_disclosure_hit_during_repaint_does_not_start_selection():
    async def run():
        app = make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await wait_ready(pilot)
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "hello"), CompleteThinking("t", 1, "reasoning"))
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            screen = app.chat_screen
            summary = screen.query_one(DisclosureSummary)
            x, y = summary.region.x + 1, summary.region.y
            # Batch-update keeps the last frame's real compositor hit map until
            # repaint, as conversation reconciliation does. No hit-test mock.
            with app.batch_update():
                await summary.remove()
                assert summary.parent is None
                assert screen.get_widget_and_offset_at(x, y)[0] is summary
                screen._forward_event(MouseDown(
                    None, x, y, 0, 0, 1, False, False, False,
                    screen_x=x, screen_y=y,
                ))
                assert screen._select_state is None
            app.request_shutdown()
    asyncio.run(run())


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
