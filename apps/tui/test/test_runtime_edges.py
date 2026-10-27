"""Regression coverage for real runtime and renderer handoffs."""

import asyncio
from datetime import UTC, datetime

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.submission import PendingMessage
from apps.tui.src.event_pipeline import AppendUserMessage, AppendAssistantDelta
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.services.chat.operations import Completion
from apps.tui.test.services.chat.test_runtime import Client
from apps.tui.src.widgets.chat.conversation import ConversationView
from apps.tui.src.widgets.chat.message_cards import AssistantMessage


def test_rebuilt_running_assistant_accepts_next_delta(tmp_path):
    async def run():
        client = Client()

        async def factory(s, c):
            return client

        app = IcarusTextualApp(
            TuiConfig(tmp_path),
            client_factory=factory,
            attachments=AttachmentsService(tmp_path),
        )
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.chat_store.apply_actions(
                (AppendUserMessage("t", "hello"), AppendAssistantDelta("t", "first"))
            )
            await app.chat_screen.refresh_chat()
            app.chat_screen.rendered_revision = 0
            await app.chat_screen.refresh_chat()
            app.chat_store.apply_actions((AppendAssistantDelta("t", " second"),))
            await app.chat_screen.refresh_chat()
            assert not app.chat_store.fatal_message
            assert (
                app.chat_screen.query_one(AssistantMessage).markdown_text
                == "first second"
            )
            app.request_shutdown()

    asyncio.run(run())


def test_status_from_same_session_old_epoch_cannot_finish_task(tmp_path):
    from apps.tui.src.services.chat.session import ChatSession
    from apps.tui.src.stores.chat import ChatStore
    from apps.tui.src.event_pipeline import create_default_projector_registry
    from apps.tui.src.domain.commands import create_default_command_registry
    from apps.tui.src.services.chat.operations import execute

    async def run():
        client = Client()

        async def factory(s, c):
            return client

        store = ChatStore()
        runtime = ChatSession(
            store,
            factory,
            AttachmentsService(tmp_path),
            create_default_projector_registry(),
            create_default_command_registry(),
        )
        (start,) = runtime.start()
        runtime.complete(await execute(start))
        (send,) = runtime.submit(PendingMessage("hello"))
        runtime.complete(await execute(send))
        op = runtime._operation("status", "t")
        runtime.epoch += 1
        runtime.complete(Completion(op, {"lifecycle": "completed"}))
        assert store.interaction.active_task_id == "t"
        await runtime.close()

    asyncio.run(run())
