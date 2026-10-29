"""Welcome and persistent chrome stay compact across Session restoration."""

import asyncio
from datetime import UTC, datetime

import pytest
from textual.widgets import Static

from packages.gateway_protocol import RuntimeUpdateModel, SessionHistoryModel, SessionSummaryModel
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.screens import SessionPicker
from apps.tui.src.widgets.chat import ConversationView, PersistentComposer, WelcomeMessage
from apps.tui.src.widgets.chat.status import SessionMetadataBar
from apps.tui.test.screens.chat.test_integration import (
    ControlledService,
    enter_text,
    make_app,
    make_session_app,
    wait_until,
)


@pytest.mark.parametrize("size", [(120, 40), (100, 32), (70, 24), (58, 24), (100, 12)])
def test_resumed_short_session_banner_starts_at_terminal_top(tmp_path, size):
    async def run():
        current, target = ControlledService(), ControlledService()
        current.session_summaries = (
            SessionSummaryModel(session_id="old-session", first_user_input="old question"),
        )
        target.session_id = "old-session"
        target.history = SessionHistoryModel(
            records=(RuntimeUpdateModel(
                workspace_key="workspace", session_id="old-session", task_id="old-task",
                type="user.message", payload={"text": "old question", "resources": []},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC), sequence=1,
            ),),
            history_cursor=1,
        )
        app, _ = make_session_app(current, {"old-session": target}, tmp_path)
        async with app.run_test(size=size) as pilot:
            await wait_until(pilot, lambda: app.chat_store.interaction.phase == RuntimePhase.READY)
            await enter_text(pilot, "/resume")
            await wait_until(pilot, lambda: isinstance(app.screen, SessionPicker))
            await pilot.press("enter")
            await wait_until(pilot, lambda: (
                app.chat_session.client is target
                and app.chat_store.interaction.phase == RuntimePhase.READY
                and app.chat_screen.rendered_revision == app.chat_store.revision
            ))
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            if view.max_scroll_y:
                # A short terminal may overflow. Reading back to the beginning
                # must still reach the very first screen row.
                view.page_up()
                await pilot.pause()
            assert view.scroll_y == 0
            assert view.query_one(WelcomeMessage).region.y == 0
            app.request_shutdown()

    asyncio.run(run())


def test_welcome_contains_only_logo_and_muted_version(tmp_path):
    async def run():
        app = make_app(ControlledService(), tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            hero = app.chat_screen.query_one(WelcomeMessage)
            text = " ".join(str(node.render()) for node in hero.query(Static))
            assert "当前版本" in text and "最新版本" in text
            assert "Workspace" not in text
            assert "Enter submit" not in text
            version = hero.query_one(".welcome-version")
            assert version.styles.color == app.chat_screen.query_one("#composer-tip").styles.color
            app.request_shutdown()

    asyncio.run(run())


def test_metadata_uses_compact_english_keys_and_emphasizes_values(tmp_path):
    async def run():
        app = make_app(ControlledService(), tmp_path)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            app.chat_store.model_label = "example-model"
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            bar = app.chat_screen.query_one(SessionMetadataBar)
            text = str(bar.render())
            assert "model example-model" in text
            assert "context —" in text
            assert "session " in text and "workspace " in text
            assert "思考" not in text and "reasoning" not in text
            assert "模型" not in text and "上下文" not in text and "会话" not in text
            segments = list(bar.render_line(0))
            key = next(s for s in segments if "model " in s.text)
            value = next(s for s in segments if "example-model" in s.text)
            assert not key.style.bold
            assert value.style.bold
            assert key.style.color != value.style.color
            app.request_shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("size", [(120, 40), (70, 24), (58, 24), (100, 12)])
def test_tips_rotate_above_composer_without_resetting_input_or_status(tmp_path, size):
    async def run():
        app = make_app(ControlledService(), tmp_path)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.chat_screen
            tip = screen.query_one("#composer-tip")
            composer = screen.query_one(PersistentComposer)
            composer.load_text("keep this draft")
            composer.move_cursor((0, 4))
            await pilot.pause()
            first = str(tip.render())
            seen = {first}
            for _ in range(12):
                tip.advance_tip()
                await pilot.pause()
                seen.add(str(tip.render()))
            assert len(seen) > 2
            app.chat_store.has_user_input = True
            app.chat_store.status_message = "Working on a task with a long status message"
            app.chat_store.interaction.phase = RuntimePhase.RUNNING
            await screen.refresh_chat()
            await pilot.pause()
            assert composer.text == "keep this draft"
            assert composer.cursor_location == (0, 4)
            assert composer.has_focus
            status = screen.query_one("#task-status")
            shell = screen.query_one("#composer-shell")
            assert tip.region.y == status.region.y
            assert status.region.right <= tip.region.x
            assert tip.region.bottom <= shell.region.y
            assert tip.region.right <= screen.region.right
            assert tip.region.height == 1
            app.request_shutdown()

    asyncio.run(run())


def test_tip_timer_rotates_without_chat_updates(tmp_path):
    async def run():
        app = make_app(ControlledService(), tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            tip = app.chat_screen.query_one("#composer-tip")
            first = str(tip.render())
            await pilot.pause(8.2)
            assert str(tip.render()) != first
            app.request_shutdown()

    asyncio.run(run())
