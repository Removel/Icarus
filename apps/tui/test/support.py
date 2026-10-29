"""Shared fake runtime helpers for ChatScreen interaction tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    RuntimeUpdateModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.client.gateway.models import (
    SubmitAccepted,
    TaskOperationResult,
)
from apps.tui.src.widgets.chat import ConversationView, WelcomeMessage


WORKSPACE = "/workspace/icarus-demo"


class SnapshotSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[RuntimeUpdateModel | None] = asyncio.Queue()
        self.closed = False

    async def next_update(self) -> RuntimeUpdateModel:
        item = await self.queue.get()
        if item is None:
            raise RuntimeError("snapshot subscription is closed")
        return item

    def publish(self, source_plugin_id: str, event: object) -> None:
        del source_plugin_id
        if self.closed:
            return
        if not isinstance(event, RuntimeUpdateModel):
            raise TypeError("SnapshotSubscription accepts RuntimeUpdateModel")
        self.queue.put_nowait(event)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.put_nowait(None)


class SnapshotService:
    session_id = "snapshot-session"
    workspace_key = "workspace"

    def __init__(self) -> None:
        self.subscription = SnapshotSubscription()
        self.submissions: list[str] = []
        self.submission_images: list[tuple[Path, ...]] = []
        self.steers: list[str] = []
        self.started = False
        self.session_summaries: tuple[SessionSummaryModel, ...] = ()

    async def start(self) -> None:
        self.started = True

    def subscribe_updates(self) -> SnapshotSubscription:
        if not self.started:
            raise RuntimeError("snapshot service is not running")
        return self.subscription

    async def get_session_history(
        self, *, after_sequence: int = 0
    ) -> SessionHistoryModel:
        return SessionHistoryModel(records=(), history_cursor=after_sequence)

    async def list_sessions(self) -> tuple[SessionSummaryModel, ...]:
        return self.session_summaries

    async def get_session_status(self) -> dict[str, object]:
        return {
            "workspace_key": self.workspace_key,
            "session_id": self.session_id,
            "lifecycle": "ready",
        }

    async def discard_empty_session(
        self, session_id: str
    ) -> DiscardEmptySessionResultModel:
        return DiscardEmptySessionResultModel(
            workspace_key=self.workspace_key,
            session_id=session_id,
            status="discarded",
        )

    async def submit(
        self,
        prompt: str,
        *,
        submission_id: str,
        resources=(),
        display_text=None,
    ) -> SubmitAccepted:
        del submission_id
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append(prompt)
        self.submission_images.append(tuple(resources))
        self.subscription.queue.put_nowait(
            RuntimeUpdateModel(
                workspace_key="workspace",
                session_id=self.session_id,
                task_id=task_id,
                type="user.message",
                payload={
                    "text": prompt if display_text is None else display_text,
                    "resources": [],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await asyncio.sleep(0)
        return SubmitAccepted(task_id=task_id, queue_position=0)

    async def close(self) -> None:
        self.started = False
        self.subscription.close()

    async def cancel_task(
        self, task_id: str, reason: str | None = None
    ) -> TaskOperationResult:
        del reason
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def steer_task(
        self,
        task_id: str,
        prompt: str,
        *,
        submission_id: str,
        resources=(),
        display_text=None,
    ) -> TaskOperationResult:
        del submission_id, resources, display_text
        self.steers.append(prompt)
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def get_task_status(self, task_id: str) -> dict[str, object]:
        return {"task_id": task_id, "lifecycle": "running"}

    async def reconnect(self) -> SnapshotSubscription:
        return self.subscription


def make_app(service: SnapshotService | None = None) -> IcarusTextualApp:
    service = service or SnapshotService()

    async def runtime_factory(session_id, create_if_missing):
        del session_id, create_if_missing
        return service

    from apps.tui.src.configs.config import TuiConfig
    from apps.tui.src.services.attachments import AttachmentsService

    return IcarusTextualApp(
        TuiConfig(workspace_path=Path(WORKSPACE)),
        client_factory=runtime_factory,
        attachments=AttachmentsService(None),
    )


async def wait_until(pilot, predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


async def wait_ready(pilot) -> None:
    conversation = pilot.app.chat_screen.query_one(ConversationView)
    await wait_until(
        pilot,
        lambda: (
            pilot.app.chat_store.interaction.phase == RuntimePhase.READY
            and conversation.is_mounted
            and len(conversation.query(WelcomeMessage)) == 1
        ),
    )
