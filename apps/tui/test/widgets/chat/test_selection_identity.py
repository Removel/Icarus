import asyncio

from textual.selection import Selection
from textual.geometry import Offset

from apps.tui.test.widgets.chat.test_conversation import ConversationTestApp, ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteAssistantMessage, AppendAssistantDelta
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown


def test_live_assistant_remount_without_card_keeps_highlight(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(AppendAssistantDelta('t', 'answer selected'))
            await pilot.pause()
            paragraph = view.query_one(StreamingMarkdown).query_one('MarkdownParagraph')
            app.screen.selections = {paragraph: Selection(Offset(0, 0), Offset(6, 0))}
            await pilot.pause()
            assert view.get_selected_text() == 'answer'
            await view._render_window(0, 1)
            await pilot.pause()
            assert view.get_selected_text() == 'answer'
            assert any(widget.is_attached and 'answer' in str(widget.get_selection(selection)) for widget, selection in app.screen.selections.items())
    asyncio.run(run())


def test_selected_assistant_identity_survives_card_insert_then_reconcile(tmp_path):
    from apps.tui.src.event_pipeline import AppendThinkingDelta
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(AppendAssistantDelta('t', 'answer selected'))
            await pilot.pause()
            paragraph = view.query_one(StreamingMarkdown).query_one('MarkdownParagraph')
            app.screen.selections = {paragraph: Selection(Offset(0, 0), Offset(6, 0))}
            await pilot.pause()
            assert view.get_selected_text() == 'answer'
            await view.apply_action(AppendThinkingDelta('t', step=1, text='reason'))
            await view._render_window(0, 1)
            await pilot.pause()
            assert view.get_selected_text() == 'answer'
            assert any('answer' in str(widget.get_selection(selection)) for widget, selection in app.screen.selections.items())
    asyncio.run(run())
