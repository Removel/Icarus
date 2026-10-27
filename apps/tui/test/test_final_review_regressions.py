"""Verified final-review regressions, tested through actual owners."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from textual.screen import Screen

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.domain.submission import PendingMessage
from apps.tui.src.event_pipeline import (
    AppendUserMessage,
    AppendAssistantDelta,
    CompleteAssistantMessage,
    AppendToolStarted,
    create_default_projector_registry,
)
from apps.tui.src.services.attachments import AttachmentsService, ClipboardRead
from apps.tui.src.services.chat.operations import Completion, PreparedSession, execute
from apps.tui.src.services.chat.session import ChatSession
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.message_cards import AssistantMessage
from apps.tui.src.widgets.chat.execution_panel import ToolBlock, RunCard
from apps.tui.src.widgets.chat.local_scroll import LocalScroll
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.src.widgets.chat.composer import PersistentComposer
from apps.tui.src.domain.submission import DraftImage
from apps.tui.test.services.chat.test_runtime import Client


def app_for(tmp_path):
    client = Client()

    async def factory(s, c):
        return client

    return IcarusTextualApp(
        TuiConfig(tmp_path),
        client_factory=factory,
        attachments=AttachmentsService(tmp_path),
    ), client


def test_advisory_status_failure_does_not_freeze_accepted_task(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        runtime = app.chat_session
        (op,) = runtime.start()
        runtime.complete(await execute(op))
        (op,) = runtime.submit(PendingMessage("hello"))
        operations = runtime.complete(await execute(op))
        status = next(op for op in operations if op.kind == "status")
        runtime.complete(Completion(status, error=ConnectionError("lookup failed")))
        assert not app.chat_store.fatal_message
        update = RuntimeUpdateModel(
            workspace_key="w",
            session_id="s",
            task_id="t",
            type="assistant.text_delta",
            payload={"step": 1, "text": "still running"},
            occurred_at=datetime.now(UTC),
        )
        runtime.receive(update, scope=runtime.scope)
        assert app.chat_store.conversation.units[-1].text == "still running"
        await runtime.close()

    asyncio.run(run())


def test_navigation_without_changes_preserves_inner_and_outer_offsets(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        async with app.run_test(size=(100, 25)) as pilot:
            await pilot.pause()
            for i in range(8):
                app.chat_store.apply_actions(
                    (
                        AppendUserMessage(str(i), "question"),
                        CompleteAssistantMessage(
                            str(i), "\n\n".join(["line " * 8] * 20)
                        ),
                    )
                )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            view._set_reading_history(True)
            view.anchor(False)
            view.scroll_to(y=8, animate=False, immediate=True)
            inner = app.chat_screen.query(LocalScroll).last()
            inner.scroll_to(y=3, animate=False, immediate=True)
            await pilot.pause()
            before = view.scroll_y, inner.scroll_y
            await app.push_screen(Screen())
            await app.pop_screen()
            await pilot.pause()
            assert app.chat_screen.query(LocalScroll).last() is inner
            assert (view.scroll_y, inner.scroll_y) == before
            app.request_shutdown()

    asyncio.run(run())


def test_snapshot_run_log_collects_next_tool_without_clipping_assistant(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (
                    AppendUserMessage("t", "q"),
                    AppendToolStarted("t", "a", "read", "{}"),
                    CompleteAssistantMessage("t", "answer", 1),
                )
            )
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            app.chat_store.apply_actions(
                (AppendToolStarted("t", "b", "search", "{}", 2),)
            )
            await app.chat_screen.refresh_chat()
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            assert [
                type(n).__name__
                for n in view.children
                if isinstance(n, (RunCard, AssistantMessage))
            ] == ["RunCard", "AssistantMessage"]
            assert len(list(view.query(RunCard))) == 1
            assert [tool.call_id for tool in view.query(ToolBlock)] == ["a", "b"]
            app.request_shutdown()

    asyncio.run(run())


def test_deferred_clipboard_image_cannot_cross_session_generation(tmp_path):
    async def run():
        app, client = app_for(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            composer = app.chat_screen.query_one(PersistentComposer)
            path = tmp_path / "late.png"
            path.write_bytes(b"image")
            value = ClipboardRead(image=DraftImage("clipboard", path, True))
            await app.push_screen(Screen())
            app.chat_screen.deliver_clipboard(composer.draft_token, value)
            replacement = Client()
            replacement.session_id = "new"
            history = await replacement.get_session_history()
            op = app.chat_session._operation("prepare", None)
            app._advance(
                app.chat_session.complete(
                    Completion(
                        op,
                        PreparedSession(replacement, replacement.subscription, history),
                    )
                )
            )
            await app.pop_screen()
            await pilot.pause()
            assert not composer.images
            assert not path.exists()
            app.request_shutdown()

    asyncio.run(run())


def test_slow_mount_does_not_render_hidden_markdown_or_ack_revision(
    tmp_path, monkeypatch
):
    async def run():
        app, client = app_for(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            view = app.chat_screen.query_one(ConversationView)
            original = view._mount_unit

            async def blocked(unit):
                if unit.kind == "assistant":
                    entered.set()
                    await release.wait()
                return await original(unit)

            monkeypatch.setattr(view, "_mount_unit", blocked)
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "q"), AppendAssistantDelta("t", "first"))
            )
            app.chat_screen.rendered_revision = 0
            task = asyncio.create_task(app.chat_screen.refresh_chat())
            await asyncio.wait_for(entered.wait(), 2)
            target_revision = app.chat_store.revision
            await app.push_screen(Screen())
            release.set()
            await task
            await pilot.pause()
            assert len(app.chat_screen.query(StreamingMarkdown)) == 0
            assert app.chat_screen.rendered_revision < target_revision
            await app.pop_screen()
            await pilot.pause()
            app.request_shutdown()

    asyncio.run(run())
