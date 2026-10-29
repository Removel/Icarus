import asyncio
from textual.screen import Screen
from apps.tui.test.test_final_review_regressions import app_for
from apps.tui.src.event_pipeline import (
    AppendUserMessage,
    CompleteAssistantMessage,
    AppendAssistantDelta,
)
from apps.tui.src.widgets.chat.conversation import ConversationView


def test_return_with_background_delta_restores_detached_anchor_after_layout(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        async with app.run_test(size=(100, 25)) as pilot:
            await pilot.pause()
            for i in range(8):
                app.chat_store.apply_actions(
                    (
                        AppendUserMessage(str(i), f"q {i}"),
                        CompleteAssistantMessage(str(i), "text\n\n" * 12),
                    )
                )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            view._set_reading_history(True)
            view.anchor(False)
            view.scroll_to(y=8, animate=False, immediate=True)
            await pilot.pause()
            before = view.scroll_y
            await app.push_screen(Screen())
            app.chat_store.apply_actions((AppendAssistantDelta("7", "background", 2),))
            await app.pop_screen()
            await pilot.pause()
            await pilot.pause()
            assert view.scroll_y == before
            assert not view.following_live_tail
            app.request_shutdown()

    asyncio.run(run())
