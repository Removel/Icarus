import asyncio
from apps.tui.src.services.chat.session import ChatSession
from apps.tui.src.services.chat.operations import execute
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.stores.chat import ChatStore
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.event_pipeline import create_default_projector_registry
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.test.services.chat.test_runtime import Client


def test_cancelled_or_stale_resume_picker_cannot_leave_session_switching():
    class Listing(Client):
        async def list_sessions(self):
            return ()

    async def run():
        client = Listing()

        async def factory(s, c):
            return client

        store = ChatStore()
        service = ChatSession(
            store,
            factory,
            AttachmentsService(None),
            create_default_projector_registry(),
            create_default_command_registry(),
        )
        (op,) = service.start()
        service.complete(await execute(op))
        (op,) = service.request_resume()
        service.complete(await execute(op))
        service.epoch += 1
        assert service.resume("wrong", request_id=op.scope.operation_id) == ()
        assert store.interaction.phase == RuntimePhase.READY
        assert service.sessions_ready is None
        await service.close()

    asyncio.run(run())
