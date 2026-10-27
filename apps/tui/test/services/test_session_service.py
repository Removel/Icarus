"""Focused tests for Gateway Session lifecycle without Textual."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.domain.chat_state import ChatState, RuntimePhase
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.gateway_client.models import UpdateSubscription
from apps.tui.src.services.session_service import (
    PreparedSession,
    SessionBusyError,
    SessionService,
)


class FakeSubscription(UpdateSubscription):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self, error: BaseException | None = None) -> None:
        self.closed = True
        super().close(error)


class FakeClient:
    def __init__(
        self,
        *,
        session_id: str = "session-1",
        history: SessionHistoryModel | None = None,
        status: dict[str, Any] | None = None,
        sessions: tuple[SessionSummaryModel, ...] = (),
        history_error: BaseException | None = None,
        close_error: BaseException | None = None,
        reconnect_error: BaseException | None = None,
    ) -> None:
        self.session_id = session_id
        self.workspace_key = "workspace-1"
        self.subscription = FakeSubscription()
        self.history = history or SessionHistoryModel(
            records=(), history_cursor=0
        )
        self.status = status or {"lifecycle": "ready"}
        self.sessions = sessions
        self.history_error = history_error
        self.close_error = close_error
        self.reconnect_error = reconnect_error
        self.started = False
        self.closed = False
        self.discarded_session_id: str | None = None
        self.history_after_sequence: int | None = None
        self.reconnect_subscription = FakeSubscription()

    async def start(self) -> None:
        self.started = True

    def subscribe_updates(self) -> FakeSubscription:
        return self.subscription

    async def get_session_history(
        self, *, after_sequence: int = 0
    ) -> SessionHistoryModel:
        self.history_after_sequence = after_sequence
        if self.history_error is not None:
            raise self.history_error
        return self.history

    async def get_session_status(self) -> dict[str, Any]:
        return self.status

    async def list_sessions(self) -> tuple[SessionSummaryModel, ...]:
        return self.sessions

    async def discard_empty_session(
        self, session_id: str
    ) -> DiscardEmptySessionResultModel:
        self.discarded_session_id = session_id
        return DiscardEmptySessionResultModel(
            workspace_key=self.workspace_key,
            session_id=session_id,
            status="discarded",
        )

    async def reconnect(self) -> FakeSubscription:
        if self.reconnect_error is not None:
            raise self.reconnect_error
        return self.reconnect_subscription

    async def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class FakeFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls: list[tuple[str | None, bool]] = []

    async def __call__(
        self, session_id: str | None, create_if_missing: bool
    ) -> FakeClient:
        self.calls.append((session_id, create_if_missing))
        return self.client


def make_service(
    client: FakeClient | None = None,
    *,
    state: ChatState | None = None,
    has_user_input: bool = False,
) -> tuple[SessionService, FakeFactory, list[str], list[tuple[str, str, str]]]:
    fake = client or FakeClient()
    factory = FakeFactory(fake)
    statuses: list[str] = []
    notices: list[tuple[str, str, str]] = []
    service = SessionService(
        factory,
        TuiConfig(workspace_path=Path("/tmp")),
        chat_state=state or ChatState(phase=RuntimePhase.READY),
        has_user_input=lambda: has_user_input,
        on_status=statuses.append,
        on_notify=lambda text, title, severity: notices.append(
            (text, title, severity)
        ),
    )
    return service, factory, statuses, notices


def test_start_prepares_client_subscription_and_history() -> None:
    service, factory, _, _ = make_service()

    result = asyncio.run(service.start(None))

    assert result.kind == "ready"
    assert result.prepared is not None
    assert result.prepared.service is factory.client
    assert result.prepared.history.records == ()
    assert factory.client.started is True
    assert service.client is None
    assert service.epoch == 0


def test_failed_candidate_is_closed_and_empty_session_discarded() -> None:
    client = FakeClient(history_error=RuntimeError("history failed"))
    service, factory, _, _ = make_service(client)

    result = asyncio.run(service.start(None))

    assert result.kind == "failed"
    assert isinstance(result.error, RuntimeError)
    assert client.subscription.closed is True
    assert client.closed is True
    assert client.discarded_session_id == "session-1"
    assert factory.calls == [(None, True)]


def test_adopt_sets_client_subscription_and_epoch() -> None:
    service, factory, _, _ = make_service()
    prepared = PreparedSession(
        factory.client,
        factory.client.subscription,
        factory.client.history,
    )

    service.adopt(prepared)

    assert service.client is factory.client
    assert service.subscription is factory.client.subscription
    assert service.session_id == "session-1"
    assert service.epoch == 1


def test_bind_chat_state_replaces_queue_owner() -> None:
    service, _, _, _ = make_service()
    replacement = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-2")

    service.bind_chat_state(replacement)

    assert service.chat_state is replacement


def test_reconnect_loads_history_after_last_sequence() -> None:
    service, factory, _, _ = make_service()
    service.adopt(
        PreparedSession(
            factory.client,
            factory.client.subscription,
            factory.client.history,
        )
    )

    result = asyncio.run(service.reconnect(last_sequence=7))

    assert result.kind == "reconnected"
    assert result.prepared is not None
    assert factory.client.history_after_sequence == 7
    assert result.prepared.subscription is factory.client.reconnect_subscription


def test_reconnect_failure_returns_failed_result() -> None:
    client = FakeClient(reconnect_error=ConnectionError("lost"))
    service, factory, _, _ = make_service(client)
    service.adopt(
        PreparedSession(client, client.subscription, client.history)
    )

    result = asyncio.run(service.reconnect(last_sequence=7))

    assert result.kind == "failed"
    assert isinstance(result.error, ConnectionError)


def test_resume_rejects_busy_session_without_activating() -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, factory, _, _ = make_service(state=state)
    service.adopt(
        PreparedSession(
            factory.client,
            factory.client.subscription,
            factory.client.history,
        )
    )
    activated: list[PreparedSession] = []

    async def activate(prepared: PreparedSession) -> bool:
        activated.append(prepared)
        return True

    async def select(sessions, current):
        return "other"

    result = asyncio.run(service.resume(select, activate))

    assert result.kind == "failed"
    assert isinstance(result.error, SessionBusyError)
    assert activated == []


def test_resume_cancelled_selection_marks_ready_without_activation() -> None:
    service, factory, statuses, _ = make_service()
    service.adopt(
        PreparedSession(
            factory.client,
            factory.client.subscription,
            factory.client.history,
        )
    )
    activated: list[PreparedSession] = []

    async def activate(prepared: PreparedSession) -> bool:
        activated.append(prepared)
        return True

    async def select(sessions, current):
        return None

    result = asyncio.run(service.resume(select, activate))

    assert result.kind == "cancelled"
    assert activated == []
    assert service.chat_state.phase == RuntimePhase.READY
    assert "" in statuses


def test_resume_prepares_and_activates_selected_session() -> None:
    service, factory, _, _ = make_service()
    service.adopt(
        PreparedSession(
            factory.client,
            factory.client.subscription,
            factory.client.history,
        )
    )
    activated: list[PreparedSession] = []

    async def activate(prepared: PreparedSession) -> bool:
        activated.append(prepared)
        return True

    async def select(sessions, current):
        return "other"

    result = asyncio.run(service.resume(select, activate))

    assert result.kind == "ready"
    assert len(activated) == 1
    assert factory.calls == [("other", False)]


def test_clear_on_empty_session_does_not_create_another() -> None:
    service, factory, statuses, notices = make_service(has_user_input=False)
    service.adopt(
        PreparedSession(
            factory.client,
            factory.client.subscription,
            factory.client.history,
        )
    )

    async def activate(prepared: PreparedSession) -> bool:
        raise AssertionError("must not activate")

    result = asyncio.run(service.clear(activate))

    assert result.kind == "cancelled"
    assert "Already in a new conversation" in statuses
    assert factory.calls == []
    assert notices and notices[0][1] == "New conversation"


def test_close_collects_errors_without_raising() -> None:
    client = FakeClient(close_error=RuntimeError("close failed"))
    service, factory, _, _ = make_service(client)
    service.adopt(
        PreparedSession(client, client.subscription, client.history)
    )

    errors = asyncio.run(service.close())

    assert client.subscription.closed is True
    assert errors and "close failed" in errors[0]
    assert service.client is None
