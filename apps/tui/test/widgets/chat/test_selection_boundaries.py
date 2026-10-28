import asyncio

from textual.geometry import Offset
from textual.selection import Selection

from apps.tui.test.widgets.chat.test_conversation import ConversationTestApp, ConversationView
from apps.tui.src.event_pipeline import (
    AppendAssistantDelta, AppendThinkingDelta, AppendUserMessage,
    CompleteAssistantMessage, CompleteThinking, AppendToolStarted,
)
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock


def test_streaming_selection_does_not_expand_or_scroll(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(60, 12)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(AppendAssistantDelta('t', '\n\n'.join(['old text'] * 20)))
            await pilot.pause()
            view.scroll_home(animate=False, immediate=True)
            paragraph = view.query_one(StreamingMarkdown).query('MarkdownParagraph').last()
            app.screen.selections = {paragraph: Selection(Offset(0, 0), None)}
            await pilot.pause()
            assert view.get_selected_text() == 'old text'
            scroll = view.scroll_y
            await view.apply_action(AppendAssistantDelta('t', ' APPENDED'))
            await pilot.pause()
            assert view.get_selected_text() == 'old text'
            assert view.scroll_y == scroll
            assert not view.following_live_tail
    asyncio.run(run())


def test_selection_never_includes_collapsed_thinking(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(CompleteThinking('t', step=1, text='secret collapsed', partial=False))
            await view.apply_action(CompleteAssistantMessage('t', '**visible** answer'))
            await pilot.pause()
            bodies = list(view._selection_bodies())
            app.screen.selections = {widget: Selection(None, None) for _, widget, _ in bodies}
            await pilot.pause()
            assert view.get_selected_text() == 'question\n\nvisible answer'
            assert 'secret' not in view.get_selected_text()
    asyncio.run(run())


def test_new_run_card_does_not_redirect_assistant_selection(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(AppendAssistantDelta('t', 'selected answer'))
            await pilot.pause()
            paragraph = view.query_one(StreamingMarkdown).query_one('MarkdownParagraph')
            app.screen.selections = {paragraph: Selection(Offset(0, 0), Offset(8, 0))}
            await pilot.pause()
            assert view.get_selected_text() == 'selected'
            await view.apply_action(AppendThinkingDelta('t', step=1, text='thinking'))
            await view.apply_action(AppendAssistantDelta('t', ' more'))
            await pilot.pause()
            assert view.get_selected_text() == 'selected'
            selected = [widget for widget in app.screen.selections if widget.is_attached]
            assert paragraph in selected
    asyncio.run(run())


def test_mouse_drag_anchor_survives_history_page_and_continues_extension(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(55):
                await view.apply_action(AppendUserMessage(str(index), f'q{index}'))
                await view.apply_action(CompleteAssistantMessage(str(index), f'a{index}'))
            await view.finish_history_restore_async()
            await pilot.pause()
            first = view.query('.message-content').first()
            view.scroll_to_widget(first, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(first, offset=(0, 0))
            view.page_up()
            await pilot.pause()
            earlier = view.query('.message-content').first()
            view.scroll_to_widget(earlier, animate=False, immediate=True)
            await pilot.pause()
            await pilot.hover(earlier, offset=(0, 0))
            await pilot.mouse_up(earlier, offset=(0, 0))
            await pilot.pause()
            text = view.get_selected_text()
            assert text.startswith('q23\n\na23')
            assert text.endswith('a30')
            assert len(view._visible_turns) == 24
    asyncio.run(run())


def test_keyboard_expands_across_twenty_four_turn_window(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(55):
                await view.apply_action(AppendUserMessage(str(index), f'q{index}'))
                await view.apply_action(CompleteAssistantMessage(str(index), f'a{index}'))
            await view.finish_history_restore_async()
            await pilot.pause()
            first = view.query('.message-content').first()
            view.scroll_to_widget(first, animate=False, immediate=True)
            await pilot.pause()
            view.begin_keyboard_selection()
            first_text = view.get_selected_text()
            assert first_text == 'q'
            for _ in range(125):
                await pilot.press('shift+up')
            text = view.get_selected_text()
            assert text.startswith('q0\n\na0\n\nq1')
            assert text.endswith('a30')
            assert view.mounted_turn_range[0] == 0
            assert len(view._visible_turns) == 24
    asyncio.run(run())
