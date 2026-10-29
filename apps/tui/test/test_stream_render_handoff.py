"""Streaming bursts must not tear down the retained conversation."""

import asyncio

import pytest
from textual.screen import Screen

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendThinkingDelta,
    CompleteThinking,
    AppendToolStarted,
    FinishTurn,
    AppendUserMessage,
    CompleteAssistantMessage,
)
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.message_cards import AssistantMessage
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.test.test_final_review_regressions import app_for


def test_stream_without_selection_does_not_walk_historical_bodies(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions((AppendUserMessage("t", "question"), AppendThinkingDelta("t", 1, "first")))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            def unexpected_scan():
                raise AssertionError("no selection: historical body scan is unnecessary")
            monkeypatch.setattr(view, "_selection_bodies", unexpected_scan)
            for _ in range(100):
                app.chat_store.apply_actions((AppendThinkingDelta("t", 1, "x"),))
            await app.chat_screen.refresh_chat()
            assert not app.chat_store.fatal_message
            from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock
            assert view.query_one(ThinkingBlock).markdown_text == "first" + "x" * 100
            app.request_shutdown()
    asyncio.run(run())


def test_unchanged_refresh_does_not_rebuild_chrome(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await app.chat_screen.refresh_chat()
            from apps.tui.src.widgets.chat.input_dock import InputDock
            dock = app.chat_screen.query_one(InputDock)
            count = 0
            original = dock.show_viewmodel
            async def tracked(*args):
                nonlocal count
                count += 1
                return await original(*args)
            monkeypatch.setattr(dock, "show_viewmodel", tracked)
            for _ in range(30):
                await app.chat_screen.refresh_chat()
            assert count == 0
            app.request_shutdown()
    asyncio.run(run())


def test_collapsed_thinking_catchup_yields_to_pending_ui_work(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions((AppendUserMessage("t", "question"), AppendThinkingDelta("t", 1, "first")))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            applied = 0
            progress = []
            original = view.apply_action
            async def tracked(action):
                nonlocal applied
                result = await original(action)
                applied += 1
                return result
            monkeypatch.setattr(view, "apply_action", tracked)
            for _ in range(128):
                app.chat_store.apply_actions((AppendThinkingDelta("t", 1, "x"),))
            asyncio.get_running_loop().call_soon(lambda: progress.append(applied))
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            assert progress and progress[0] < 128
            assert applied == 128
            app.request_shutdown()
    asyncio.run(run())


def test_chat_changed_burst_coalesces_view_notifications(tmp_path, monkeypatch):
    async def run():
        app, _ = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            from apps.tui.src.screens.chat.messages import ChatChanged
            count = 0
            original = app.chat_screen.refresh_chat
            async def tracked():
                nonlocal count
                count += 1
                await original()
            monkeypatch.setattr(app.chat_screen, "refresh_chat", tracked)
            for _ in range(100):
                app.chat_store.apply_actions((AppendThinkingDelta("t", 1, "x"),))
                app.chat_screen.post_message(ChatChanged(app.chat_store.revision))
            await pilot.pause()
            assert count <= 2
            from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock
            assert app.chat_screen.query_one(ThinkingBlock).markdown_text == "x" * 100
            app.request_shutdown()
    asyncio.run(run())


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
