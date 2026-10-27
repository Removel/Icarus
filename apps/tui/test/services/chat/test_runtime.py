import asyncio
from pathlib import Path

from apps.tui.src.domain.submission import PendingMessage
from apps.tui.src.client.gateway.models import SubmitAccepted
from apps.tui.src.client.gateway.subscription import UpdateSubscription
from apps.tui.src.event_pipeline import create_default_projector_registry
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.services.attachments import AttachmentsService
from apps.tui.src.stores.chat import ChatStore


class Client:
    session_id = "s"
    workspace_key = "w"

    def __init__(self):
        self.subscription = UpdateSubscription()
        self.attempts = []
        self.closed = False

    async def start(self):
        pass

    def subscribe_updates(self):
        return self.subscription

    async def get_session_history(self, *, after_sequence=0):
        from packages.gateway_protocol import SessionHistoryModel

        return SessionHistoryModel(records=(), history_cursor=after_sequence)

    async def get_session_status(self):
        return {"lifecycle": "ready"}

    async def submit(self, prompt, **kwargs):
        self.attempts.append((prompt, kwargs))
        return SubmitAccepted("t", 0)

    async def get_task_status(self, task_id):
        return {"lifecycle": "running"}

    async def close(self):
        self.closed = True

    async def discard_empty_session(self, session_id):
        pass


def test_completion_only_changes_store_on_apply_and_rejects_duplicate(tmp_path):
    from apps.tui.src.services.chat.session import ChatSession
    from apps.tui.src.services.chat.operations import execute

    async def run():
        client = Client()

        async def factory(session_id, create):
            return client

        store = ChatStore()
        service = ChatSession(
            store,
            factory,
            AttachmentsService(tmp_path),
            create_default_projector_registry(),
            create_default_command_registry(),
        )
        (operation,) = service.start()
        result = await execute(operation)
        assert service.client is None
        service.complete(result)
        (operation,) = service.submit(PendingMessage("hello", submission_id="stable"))
        result = await execute(operation)
        assert store.interaction.active_task_id is None
        service.complete(result)
        assert store.interaction.active_task_id == "t"
        revision = store.revision
        assert service.complete(result) == ()
        assert store.revision == revision
        assert client.attempts[0][1]["submission_id"] == "stable"
        await service.close()

    asyncio.run(run())
