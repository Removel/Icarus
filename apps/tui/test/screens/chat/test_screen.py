"""Focused ChatScreen composition and lifecycle tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.domain.chat_state import RuntimePhase
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.client.gateway.models import (
    SubmitAccepted,
    TaskOperationResult,
)
from apps.tui.src.client.gateway.subscription import UpdateSubscription
from apps.tui.src.screens.chat import ChatScreen
from apps.tui.src.widgets.chat import PersistentComposer


class FakeSubscription(UpdateSubscription):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self, error: BaseException | None = None) -> None:
        self.closed = True
        super().close(error)


class FakeService:
    session_id = "session-1"
    workspace_key = "workspace-1"

    def __init__(self) -> None:
        self.subscription = FakeSubscription()
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    def subscribe_updates(self) -> FakeSubscription:
        return self.subscription

    async def get_session_history(
        self, *, after_sequence: int = 0
    ) -> SessionHistoryModel:
        return SessionHistoryModel(records=(), history_cursor=after_sequence)

    async def get_session_status(self) -> dict[str, Any]:
        return {"lifecycle": "ready"}

    async def list_sessions(self) -> tuple[SessionSummaryModel, ...]:
        return ()

    async def discard_empty_session(
        self, session_id: str
    ) -> DiscardEmptySessionResultModel:
        return DiscardEmptySessionResultModel(
            workspace_key=self.workspace_key,
            session_id=session_id,
            status="discarded",
        )

    async def submit(self, prompt: str, **kwargs: Any) -> SubmitAccepted:
        del prompt, kwargs
        return SubmitAccepted(task_id="task-1", queue_position=0)

    async def steer_task(
        self, task_id: str, prompt: str, **kwargs: Any
    ) -> TaskOperationResult:
        del prompt, kwargs
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def cancel_task(
        self, task_id: str, reason: str | None = None
    ) -> TaskOperationResult:
        del reason
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def reconnect(self) -> FakeSubscription:
        return FakeSubscription()

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        del task_id
        return {"lifecycle": "completed"}

    async def close(self) -> None:
        self.closed = True


def make_app(service: FakeService) -> IcarusTextualApp:
    async def runtime_factory(
        session_id: str | None, create_if_missing: bool
    ) -> FakeService:
        del session_id, create_if_missing
        return service

    return IcarusTextualApp(
        TuiConfig(workspace_path=Path("/tmp/icarus-chat-screen")),
        client_factory=runtime_factory,
        attachments=__import__(
            "apps.tui.src.services.attachments", fromlist=["AttachmentsService"]
        ).AttachmentsService(None),
    )


async def wait_until(pilot, predicate) -> None:
    async def wait() -> None:
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


def test_chat_screen_composes_shell_and_focuses_composer() -> None:
    async def run():
        service = FakeService()
        app = make_app(service)
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_until(
                pilot, lambda: app.chat_store.interaction.phase == RuntimePhase.READY
            )
            screen = app.screen
            result = (
                isinstance(screen, ChatScreen),
                screen.query_one("#conversation") is not None,
                screen.query_one("#turn-rail") is not None,
                screen.query_one("#queue-panel") is not None,
                screen.query_one("#composer") is not None,
                screen.query_one("#task-status") is not None,
                screen.focused is screen.query_one(PersistentComposer),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.closed)
        return result

    assert asyncio.run(run()) == (True, True, True, True, True, True, True)


def test_chat_screen_sets_narrow_and_short_classes() -> None:
    async def run(size):
        service = FakeService()
        app = make_app(service)
        async with app.run_test(size=size) as pilot:
            await wait_until(
                pilot, lambda: app.chat_store.interaction.phase == RuntimePhase.READY
            )
            screen = app.screen
            result = (
                screen.has_class("-narrow"),
                screen.has_class("-compact-logo"),
                screen.has_class("-short"),
            )
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.closed)
        return result

    assert asyncio.run(run((58, 24))) == (True, True, False)
    assert asyncio.run(run((100, 12))) == (False, False, True)


def test_chat_screen_shutdown_closes_runtime() -> None:
    async def run():
        service = FakeService()
        app = make_app(service)
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_until(
                pilot, lambda: app.chat_store.interaction.phase == RuntimePhase.READY
            )
            screen = app.screen
            app.request_shutdown(return_code=0)
            await wait_until(pilot, lambda: service.closed)
            return service.subscription.closed

    assert asyncio.run(run()) is True
