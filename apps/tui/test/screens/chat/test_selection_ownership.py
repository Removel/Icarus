import asyncio

from textual.document._document import Selection
from textual.geometry import Offset
from textual.selection import Selection as BodySelection

from apps.tui.test.support import make_app, wait_ready
from apps.tui.src.services.desktop import DesktopService
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage


def test_new_composer_selection_takes_ownership_from_chat_selection():
    async def run():
        app = make_app()
        copied = []
        app.desktop = DesktopService(write_text=copied.append)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            app.chat_store.apply_actions((AppendUserMessage('t', 'chat text'),))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            body = view.query_one('.message-content')
            app.screen.selections = {body: BodySelection(Offset(0, 0), Offset(4, 0))}
            await pilot.pause()
            assert view.get_selected_text() == 'chat'
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text('composer text')
            composer.selection = Selection((0, 0), (0, 8))
            await pilot.pause()
            await pilot.press('ctrl+c')
            await pilot.pause()
            assert copied == ['composer']
            assert not view.has_selection()
    asyncio.run(run())


def test_chat_copy_failure_does_not_clear_draft_or_interrupt():
    async def run():
        app = make_app()
        def fail(text):
            raise OSError('busy')
        app.desktop = DesktopService(write_text=fail)
        async with app.run_test() as pilot:
            await wait_ready(pilot)
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text('unchanged')
            composer.selection = Selection((0, 0), (0, 9))
            await pilot.press('ctrl+c')
            await pilot.pause()
            assert composer.text == 'unchanged'
            assert composer.get_selection_text() == 'unchanged'
    asyncio.run(run())
