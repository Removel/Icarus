"""Protocols the TUI needs from the Gateway client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    ResourceRefModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
    UpdateSubscription,
)


class RuntimeClient(Protocol):
    session_id: str | None
    workspace_key: str | None

    async def start(self) -> None:
        ...

    def subscribe_updates(self) -> UpdateSubscription:
        ...

    async def get_session_history(
        self, *, after_sequence: int = 0
    ) -> SessionHistoryModel:
        ...

    async def list_sessions(self) -> tuple[SessionSummaryModel, ...]:
        ...

    async def get_session_status(self) -> dict[str, Any]:
        ...

    async def discard_empty_session(
        self, session_id: str
    ) -> DiscardEmptySessionResultModel:
        ...

    async def submit(
        self,
        prompt: str,
        *,
        submission_id: str,
        resources: tuple[ResourceRefModel, ...] = (),
        display_text: str | None = None,
    ) -> SubmitAccepted:
        ...

    async def steer_task(
        self,
        task_id: str,
        prompt: str,
        *,
        submission_id: str,
        resources: tuple[ResourceRefModel, ...] = (),
        display_text: str | None = None,
    ) -> TaskOperationResult:
        ...

    async def cancel_task(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> TaskOperationResult:
        ...

    async def reconnect(self) -> UpdateSubscription:
        ...

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        ...

    async def close(self) -> None:
        ...


ClientFactory = Callable[[str | None, bool], Awaitable[RuntimeClient]]
