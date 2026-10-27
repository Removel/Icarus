"""Session workflow tests exercise prepare/execute/complete like the real App."""

import asyncio
from pathlib import Path
import pytest

from apps.tui.test.services.chat.test_runtime import Client
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.services.chat.session import ChatSession
from apps.tui.src.services.chat.operations import execute, Completion, PreparedSession
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.event_pipeline import create_default_projector_registry


def make_service(client=None, *, factory=None):
    client = client or Client()

    async def default_factory(session_id, create):
        return client

    store = ChatStore()
    service = ChatSession(
        store,
        factory or default_factory,
        AttachmentsService(None),
        create_default_projector_registry(),
        create_default_command_registry(),
    )
    return service, store, client


async def start(service):
    (op,) = service.start()
    result = await execute(op)
    service.complete(result)
    return result


def test_start_prepares_then_adopts_client_and_history():
    async def run():
        service, store, client = make_service()
        (op,) = service.start()
        result = await execute(op)
        assert service.client is None
        service.complete(result)
        assert service.client is client
        assert store.interaction.phase == RuntimePhase.READY
        assert service.epoch == 1 and store.generation == 1
        await service.close()

    asyncio.run(run())


def test_failed_candidate_is_closed_and_remains_failed():
    class Failing(Client):
        async def get_session_history(self, **kwargs):
            raise RuntimeError("history failed")

    async def run():
        service, store, client = make_service(Failing())
        result = await start(service)
        assert result.error is not None and client.closed
        assert store.interaction.phase == RuntimePhase.FAILED
        await service.close()

    asyncio.run(run())


def test_reconnect_loads_history_after_last_sequence():
    class Reconnecting(Client):
        async def reconnect(self):
            return self.subscription

        async def get_session_history(self, *, after_sequence=0):
            self.after = after_sequence
            return await super().get_session_history(after_sequence=after_sequence)

    async def run():
        service, store, client = make_service(Reconnecting())
        await start(service)
        service.updates.sequence = 7
        (op,) = service.reconnect(ConnectionError("lost"))
        service.complete(await execute(op))
        assert client.after == 7
        assert store.interaction.phase == RuntimePhase.READY
        await service.close()

    asyncio.run(run())


def test_resume_cancelled_selection_marks_ready_without_preparation():
    class Listing(Client):
        async def list_sessions(self):
            return ()

    async def run():
        service, store, client = make_service(Listing())
        await start(service)
        (op,) = service.request_resume()
        service.complete(await execute(op))
        assert service.resume(None, request_id=op.scope.operation_id) == ()
        assert store.interaction.phase == RuntimePhase.READY
        await service.close()

    asyncio.run(run())


def test_clear_on_empty_session_does_not_create_another():
    async def run():
        service, store, client = make_service()
        await start(service)
        assert service.clear() == ()
        assert service.client is client
        assert store.status_message == "Already in a new conversation"
        await service.close()

    asyncio.run(run())


def test_resume_rejects_busy_without_network_operation():
    async def run():
        service, store, client = make_service()
        await start(service)
        store.interaction.phase = RuntimePhase.RUNNING
        store.interaction.active_task_id = "t"
        assert service.request_resume() == ()
        await service.close()

    asyncio.run(run())


def test_close_collects_subscription_and_client_errors():
    class Bad(Client):
        async def close(self):
            raise RuntimeError("client failed")

    async def run():
        service, store, client = make_service(Bad())
        await start(service)

        def fail():
            raise RuntimeError("subscription failed")

        client.subscription.close = fail
        errors = await service.close()
        assert any("subscription failed" in e for e in errors)
        assert any("client failed" in e for e in errors)
        assert service.client is None

    asyncio.run(run())
