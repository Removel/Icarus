import asyncio

from textual.geometry import Offset
from textual.selection import Selection

from apps.tui.test.widgets.chat.test_conversation import ConversationTestApp, ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteAssistantMessage, AppendAssistantDelta
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown


def test_rendered_selection_excludes_labels_and_survives_replacement(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage("a", "中文😀问题"))
            await view.apply_action(AppendAssistantDelta("a", "**answer** tail"))
            await pilot.pause()
            user = view.query_one(".message-content")
            paragraph = view.query_one(StreamingMarkdown).query_one("MarkdownParagraph")
            app.screen.selections = {user: Selection(Offset(1, 0), None), paragraph: Selection(None, Offset(6, 0))}
            await pilot.pause()
            assert view.get_selected_text() == "文😀问题\n\nanswer"
            await view.apply_action(CompleteAssistantMessage("a", "**answer** replaced tail"))
            await pilot.pause()
            assert view.get_selected_text() == "文😀问题\n\nanswer"
            view.action_cancel_selection()
            assert not view.has_selection()
    asyncio.run(run())


def test_selected_text_survives_history_window_remount(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(30):
                await view.apply_action(AppendUserMessage(str(index), f"question {index}"))
                await view.apply_action(CompleteAssistantMessage(str(index), f"answer {index}"))
            await view.finish_history_restore_async()
            await pilot.pause()
            content = view.query(".message-content").first()
            app.screen.selections = {content: Selection(Offset(0, 0), Offset(10, 0))}
            await pilot.pause()
            assert view.get_selected_text() == "question 6"
            await view._page_window(-8)
            await pilot.pause()
            assert view.get_selected_text() == "question 6"
            assert view.mounted_turn_range == (0, 24)
    asyncio.run(run())


def test_native_mouse_drag_crosses_bodies_and_preserves_exact_rendered_text(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage("a", "中文😀问题"))
            await view.apply_action(CompleteAssistantMessage("a", "**answer** tail"))
            await pilot.pause()
            user = view.query_one(".message-content")
            paragraph = view.query_one(StreamingMarkdown).query_one("MarkdownParagraph")
            view.scroll_to_widget(user, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(user, offset=(0, 0))
            await pilot.hover(paragraph, offset=(5, 0))
            await pilot.mouse_up(paragraph, offset=(5, 0))
            await pilot.pause()
            assert view.get_selected_text() == "中文😀问题\n\nanswer"
            view.action_cancel_selection()
            view.scroll_to_widget(user, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(user, offset=(2, 0))
            await pilot.hover(user, offset=(6, 0))
            await pilot.mouse_up(user, offset=(6, 0))
            await pilot.pause()
            assert view.get_selected_text() == "文😀问"
    asyncio.run(run())


def test_keyboard_selection_can_expand_and_cancel(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage("a", "中文😀问题"))
            await pilot.pause()
            view.begin_keyboard_selection()
            await pilot.press("shift+right", "shift+right")
            assert view.get_selected_text() == "中文😀"
            view.action_cancel_selection()
            assert view.get_selected_text() is None
    asyncio.run(run())
