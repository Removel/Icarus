"""Streaming bursts must not tear down the retained conversation."""

import asyncio

import pytest
from textual.screen import Screen

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendUserMessage,
    CompleteAssistantMessage,
)
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.message_cards import AssistantMessage
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.test.test_final_review_regressions import app_for


def test_navigation_snapshot_does_not_replay_consumed_deltas(tmp_path):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            for index in range(30):
                app.chat_store.apply_actions((AppendUserMessage(str(index), f"q {index}"),))
            app.chat_store.apply_actions((AppendAssistantDelta("29", "first"),))
            await app.chat_screen.refresh_chat()
            view = app.chat_screen.query_one(ConversationView)
            await view.jump_to_turn(0)
            app.chat_store.apply_actions((AppendAssistantDelta("29", " second"),))
            app.chat_store.apply_actions((AppendAssistantDelta("29", " third"),))
            await view.jump_to_turn(29)
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            assert view.query_one(AssistantMessage).markdown_text == "first second third"
            app.request_shutdown()

    asyncio.run(run())


def test_cancelled_catch_up_rebuilds_before_retry(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "question"), AppendAssistantDelta("t", "first"))
            )
            await app.chat_screen.refresh_chat()
            view = app.chat_screen.query_one(ConversationView)
            original = view.apply_action
            entered = asyncio.Event()

            async def blocked(action):
                if isinstance(action, AppendAssistantDelta) and action.text == " third":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(action)

            monkeypatch.setattr(view, "apply_action", blocked)
            app.chat_store.apply_actions((AppendAssistantDelta("t", " second"),))
            app.chat_store.apply_actions((AppendAssistantDelta("t", " third"),))
            task = asyncio.create_task(app.chat_screen.refresh_chat())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            monkeypatch.setattr(view, "apply_action", original)
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            assert view.query_one(AssistantMessage).markdown_text == "first second third"
            app.request_shutdown()

    asyncio.run(run())


def test_burst_updates_keep_existing_message_and_render_every_delta(tmp_path):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "question"), AppendAssistantDelta("t", "first"))
            )
            await app.chat_screen.refresh_chat()
            message = app.chat_screen.query_one(AssistantMessage)
            for text in (" second", " third", " fourth"):
                app.chat_store.apply_actions((AppendAssistantDelta("t", text),))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            current = app.chat_screen.query_one(AssistantMessage)
            assert current is message
            assert current.markdown_text == "first second third fourth"
            assert current.query_one(StreamingMarkdown).source == "first second third fourth"
            assert not app.chat_store.fatal_message
            app.request_shutdown()

    asyncio.run(run())


def test_burst_of_new_turns_keeps_navigation_bound_to_each_question(tmp_path):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions((AppendUserMessage("0", "question 0"),))
            await app.chat_screen.refresh_chat()
            for index in range(1, 4):
                app.chat_store.apply_actions(
                    (AppendUserMessage(str(index), f"question {index}"),)
                )
            await app.chat_screen.refresh_chat()
            view = app.chat_screen.query_one(ConversationView)
            assert [(index, group[0].message_text) for index, group in view._visible_turns] == [
                (0, "question 0"),
                (1, "question 1"),
                (2, "question 2"),
                (3, "question 3"),
            ]
            assert view.mounted_turn_range == (0, 4)
            app.request_shutdown()

    asyncio.run(run())


def test_interrupted_reconcile_never_leaves_conversation_hidden(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "question"), CompleteAssistantMessage("t", "answer"))
            )
            await app.chat_screen.refresh_chat()
            view = app.chat_screen.query_one(ConversationView)
            snapshot = app.chat_store.capture_window(0, 1)
            original = view._mount_unit
            visible_during_mount = []

            async def interrupt(unit):
                visible_during_mount.append(view.visible)
                widget = await original(unit)
                view.render_valid = lambda: False
                return widget

            monkeypatch.setattr(view, "_mount_unit", interrupt)
            await view.reconcile(snapshot)
            assert visible_during_mount == [True]
            assert view.visible
            app.request_shutdown()

    asyncio.run(run())


def test_background_burst_preserves_existing_message_on_resume(tmp_path):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "question"), AppendAssistantDelta("t", "first"))
            )
            await app.chat_screen.refresh_chat()
            message = app.chat_screen.query_one(AssistantMessage)
            await app.push_screen(Screen())
            app.chat_store.apply_actions((AppendAssistantDelta("t", " second"),))
            app.chat_store.changed()
            app.chat_store.apply_actions((AppendAssistantDelta("t", " third"),))
            await app.pop_screen()
            await pilot.pause()
            current = app.chat_screen.query_one(AssistantMessage)
            assert current is message
            assert current.markdown_text == "first second third"
            assert current.query_one(StreamingMarkdown).source == "first second third"
            app.request_shutdown()

    asyncio.run(run())
