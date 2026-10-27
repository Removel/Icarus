import asyncio
from textual.screen import Screen
from apps.tui.test.test_final_review_regressions import app_for
from apps.tui.src.event_pipeline import (
    AppendUserMessage,
    CompleteAssistantMessage,
    AppendAssistantDelta,
)
from apps.tui.src.widgets.chat.local_scroll import LocalScroll
from apps.tui.src.widgets.chat.conversation import ConversationView


def test_inner_message_reading_offset_survives_background_reconcile(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        async with app.run_test(size=(100, 25)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (
                    AppendUserMessage("t", "question\n" * 60),
                    CompleteAssistantMessage("t", "answer"),
                )
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            view._set_reading_history(True)
            view.anchor(False)
            inner = app.chat_screen.query(LocalScroll).last()
            inner.scroll_to(y=5, animate=False, immediate=True)
            await pilot.pause()
            before = inner.scroll_y
            await app.push_screen(Screen())
            app.chat_store.apply_actions(
                (AppendAssistantDelta("t", "new progress", 2),)
            )
            app.chat_store.apply_actions((AppendAssistantDelta("t", " more", 2),))
            await app.pop_screen()
            await pilot.pause()
            restored = list(app.chat_screen.query(LocalScroll))[0]
            assert restored.scroll_y == before, (
                (
                    view._reading_offsets,
                    restored.max_scroll_y,
                    getattr(restored, "_pending_read_offset", None),
                    view.render_valid(),
                ),
                [
                    (type(w).__name__, getattr(w, "_unit_key", None))
                    for w in view.children
                ],
            )
            app.request_shutdown()

    asyncio.run(run())
