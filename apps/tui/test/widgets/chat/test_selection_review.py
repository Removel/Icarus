import asyncio

from textual.selection import Selection
from textual.geometry import Offset

from apps.tui.test.widgets.chat.test_conversation import ConversationTestApp, ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage, AppendToolStarted, CompleteThinking
from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock, ToolBlock


def test_selected_tools_and_thinking_follow_rendered_order_not_identity_sort(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(100, 50)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(AppendToolStarted(task_id='t', call_id='z', tool_name='first', arguments_json='{"z": 1}'))
            await view.apply_action(AppendToolStarted(task_id='t', call_id='a', tool_name='second', arguments_json='{"a": 2}'))
            await view.apply_action(CompleteThinking('t', step=1, text='VISIBLE REASON'))
            for tool in view.query(ToolBlock):
                tool.set_expanded(True)
            view.query_one(ThinkingBlock).set_expanded(True)
            await pilot.pause()
            bodies = list(view._selection_bodies())
            app.screen.selections = {widget: Selection(None, None) for _, widget, _ in bodies}
            await pilot.pause()
            expected = 'question\n\nArguments\n{\n  "z": 1\n}\nArguments\n{\n  "a": 2\n}\nVISIBLE REASON'
            assert view.get_selected_text() == expected
            view.action_cancel_selection()
            view._selection.anchor = (bodies[0][0], 0)
            view._selection.extend(bodies, bodies[-1][0], len(bodies[-1][2]))
            assert view.get_selected_text() == expected
    asyncio.run(run())


def test_collapsing_selected_thinking_cancels_hidden_text(tmp_path):
    async def run():
        app = ConversationTestApp(tmp_path)
        async with app.run_test(size=(80, 30)) as pilot:
            view = app.query_one(ConversationView)
            await view.apply_action(AppendUserMessage('t', 'question'))
            await view.apply_action(CompleteThinking('t', step=1, text='VISIBLE REASON'))
            thinking = view.query_one(ThinkingBlock)
            thinking.set_expanded(True)
            await pilot.pause()
            paragraph = thinking.query_one('MarkdownParagraph')
            app.screen.selections = {paragraph: Selection(Offset(0, 0), Offset(7, 0))}
            await pilot.pause()
            assert view.get_selected_text() == 'VISIBLE'
            thinking.set_expanded(False)
            await pilot.pause()
            assert view.get_selected_text() is None
            assert not view.has_selection()
    asyncio.run(run())
