import asyncio
from textual.app import App
from apps.tui.test.widgets.chat.test_conversation import ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteThinking


def test_explicit_jump_waits_for_inflight_history_window(tmp_path):
    class Host(App):
        def compose(self):
            yield ConversationView(tmp_path, id="conversation")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for i in range(30):
                await view.apply_action(AppendUserMessage(str(i), f"question {i}"))
                await view.apply_action(CompleteThinking(str(i), 1, f"thinking {i}"))
            view.finish_history_restore()
            await view.jump_to_turn(0)
            await pilot.pause()
            assert view.mounted_turn_range == (0, 24)
            from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock

            assert view.query_one(ThinkingBlock).markdown_text == "thinking 0"

    asyncio.run(run())
