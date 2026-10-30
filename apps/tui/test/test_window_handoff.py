import asyncio

import pytest
from textual.app import App
from apps.tui.test.widgets.chat.test_conversation import ConversationView
from apps.tui.src.event_pipeline import AppendUserMessage, CompleteThinking, CompleteAssistantMessage
from apps.tui.src.widgets.chat.message_cards import UserMessage, AssistantMessage


def test_paging_reuses_overlapping_turns_and_preserves_reader_offset(tmp_path):
    class Host(App):
        CSS = ".message { height: auto; margin-bottom: 1; }"

        def compose(self):
            yield ConversationView(tmp_path, id="conversation")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(40):
                await view.apply_action(AppendUserMessage(str(index), f"question {index}"))
                await view.apply_action(CompleteAssistantMessage(str(index), "\n\n".join(["body " * 20] * 4)))
            await view.finish_history_restore_async()
            await pilot.pause()
            shared = {index: tuple(widgets) for index, widgets in view._visible_turns if index < 32}
            anchor = next(w for index, widgets in view._visible_turns if index == 20 for w in widgets if isinstance(w, UserMessage))
            view.anchor(False)
            view.scroll_to_widget(anchor, top=True, animate=False, immediate=True)
            await pilot.pause()
            before = anchor.region.y
            await view._page_window(-8)
            await pilot.pause()
            after = {index: tuple(widgets) for index, widgets in view._visible_turns}
            assert view.mounted_turn_range == (8, 32)
            assert len(view._visible_turns) == 24
            assert all(after[index] == widgets for index, widgets in shared.items())
            assert anchor.region.y == before
            assert len(view.query(UserMessage)) == 24
            assert len(view.query(AssistantMessage)) == 24
    asyncio.run(run())


def test_screen_refresh_keeps_reused_turns_when_navigation_has_no_pending_updates(tmp_path):
    from apps.tui.test.support import make_app, wait_ready

    async def run():
        app = make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await wait_ready(pilot)
            app.chat_store.apply_actions(tuple(action for index in range(40) for action in (
                AppendUserMessage(str(index), f"question {index}"),
                CompleteAssistantMessage(str(index), "\n\n".join(["paragraph text"] * 8)),
            )))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            shared = {index: tuple(widgets) for index, widgets in view._visible_turns if index < 32}
            await view._page_window(-8)
            await pilot.pause()
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            current = dict(view._visible_turns)
            assert all(tuple(current[index]) == widgets for index, widgets in shared.items())
            app.request_shutdown()
    asyncio.run(run())


def test_cancelled_window_shift_recovers_before_next_page(tmp_path, monkeypatch):
    class Host(App):
        CSS = ".message { height: auto; margin-bottom: 1; }"
        def compose(self):
            yield ConversationView(tmp_path, id="conversation")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(40):
                await view.apply_action(AppendUserMessage(str(index), f"question {index}"))
            await view.finish_history_restore_async()
            await pilot.pause()
            original = view._mount_unit
            entered = asyncio.Event()
            async def blocked(unit):
                entered.set()
                await asyncio.Event().wait()
            monkeypatch.setattr(view, "_mount_unit", blocked)
            first = asyncio.create_task(view._page_window(-8))
            await asyncio.wait_for(entered.wait(), 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            monkeypatch.setattr(view, "_mount_unit", original)
            await view._page_window(-1)
            await pilot.pause()
            assert len(view._visible_turns) == 24
            assert len(view.query(UserMessage)) == 24
            assert all(widget.parent is view for _, widgets in view._visible_turns for widget in widgets)
    asyncio.run(run())


def test_explicit_jump_wins_over_pending_page_anchor(tmp_path):
    class Host(App):
        CSS = ".message { height: auto; margin-bottom: 1; }"
        def compose(self):
            yield ConversationView(tmp_path, id="conversation")

    async def run():
        app = Host()
        async with app.run_test(size=(80, 24)) as pilot:
            view = app.query_one(ConversationView)
            view.begin_history_restore()
            for index in range(40):
                await view.apply_action(AppendUserMessage(str(index), f"question {index}"))
                await view.apply_action(CompleteAssistantMessage(str(index), "\n\n".join(["body " * 20] * 4)))
            await view.finish_history_restore_async()
            await pilot.pause()
            anchor = next(w for index, widgets in view._visible_turns if index == 20 for w in widgets if isinstance(w, UserMessage))
            view.anchor(False)
            view.scroll_to_widget(anchor, top=True, animate=False, immediate=True)
            await pilot.pause()
            await view._page_window(-8)
            await view.jump_to_turn(12)
            await pilot.pause()
            target = next(w for index, widgets in view._visible_turns if index == 12 for w in widgets if isinstance(w, UserMessage))
            assert view.current_turn_index == 12
            assert target.region.y == view.region.y
    asyncio.run(run())


def test_explicit_jump_waits_for_inflight_history_window(tmp_path):
    class Host(App):
        CSS = ".message { height: auto; margin-bottom: 1; }"

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
