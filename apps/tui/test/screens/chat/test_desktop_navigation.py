import asyncio

from textual.widgets import ListView
from textual.geometry import Offset
from textual.selection import Selection

from apps.tui.test.support import make_app, wait_ready
from apps.tui.src.services.desktop import DesktopService
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteAssistantMessage
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.src.screens.link_picker import LinkPicker


async def show_messages(app, pilot):
    actions = (AppendUserMessage('t', 'question'), CompleteAssistantMessage('t', 'see https://example.com'))
    app.chat_store.apply_actions(actions)
    await app.chat_screen.refresh_chat()
    await pilot.pause()
    view = app.chat_screen.query_one(ConversationView)
    view.scroll_end(animate=False, immediate=True)
    await pilot.pause()
    return view


def test_keyboard_link_picker_selects_message_then_target():
    async def run():
        app = make_app()
        opened = []
        app.desktop = DesktopService(open_url=opened.append)
        async with app.run_test(size=(100, 40)) as pilot:
            await wait_ready(pilot)
            await show_messages(app, pilot)
            await pilot.press('ctrl+shift+o')
            await pilot.pause()
            assert isinstance(app.screen, LinkPicker)
            await pilot.press('enter')
            await pilot.pause()
            assert app.screen.query_one(ListView).children
            await pilot.press('enter')
            await pilot.pause()
            assert opened == ['https://example.com']
    asyncio.run(run())


def test_chat_selection_ctrl_c_preserves_draft_and_escape_clears():
    async def run():
        app = make_app()
        copied = []
        app.desktop = DesktopService(write_text=copied.append)
        async with app.run_test(size=(100, 40)) as pilot:
            await wait_ready(pilot)
            view = await show_messages(app, pilot)
            composer = app.chat_screen.query_one(PersistentComposer)
            composer.load_text('draft')
            await pilot.pause()
            body = view.query_one('.message-content')
            app.screen.selections = {body: Selection(Offset(0, 0), Offset(8, 0))}
            await pilot.pause()
            await pilot.press('ctrl+c')
            await pilot.pause()
            assert copied == ['question']
            assert composer.text == 'draft'
            await pilot.press('escape')
            assert not view.has_selection()
    asyncio.run(run())


def test_real_click_opens_bare_markdown_url_but_drag_does_not():
    async def run():
        app = make_app()
        opened = []
        app.desktop = DesktopService(open_url=opened.append)
        async with app.run_test(size=(100, 40)) as pilot:
            await wait_ready(pilot)
            view = await show_messages(app, pilot)
            paragraph = view.query_one(StreamingMarkdown).query_one('MarkdownParagraph')
            await pilot.click(paragraph, offset=(7, 0))
            await pilot.pause(app.CLICK_CHAIN_TIME_THRESHOLD + 0.1)
            assert opened == ['https://example.com']
            opened.clear()
            await pilot.mouse_down(paragraph, offset=(7, 0))
            await pilot.hover(paragraph, offset=(16, 0))
            await pilot.mouse_up(paragraph, offset=(16, 0))
            await pilot.pause()
            assert opened == []
            view.action_cancel_selection()
            await pilot.click(paragraph, offset=(7, 0), times=2)
            await pilot.pause(app.CLICK_CHAIN_TIME_THRESHOLD + 0.1)
            assert opened == []
    asyncio.run(run())
