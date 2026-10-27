"""Focused tests for queue dispatch without Textual."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.tui.src.domain.chat_state import ChatState, RuntimePhase
from apps.tui.src.gateway_client import GatewayClientError, GatewayTransportError
from apps.tui.src.gateway_client.models import SubmitAccepted, TaskOperationResult
from apps.tui.src.services.dispatch_service import DispatchService


class FakeClient:
    session_id = "session-1"
    workspace_key = "workspace-1"

    def __init__(
        self,
        *,
        submit_result: SubmitAccepted | None = None,
        submit_error: BaseException | None = None,
        steer_status: str = "accepted",
        steer_error: BaseException | None = None,
        cancel_status: str = "accepted",
        cancel_error: BaseException | None = None,
    ) -> None:
        self.submit_result = submit_result or SubmitAccepted(
            task_id="task-1", queue_position=0
        )
        self.submit_error = submit_error
        self.steer_status = steer_status
        self.steer_error = steer_error
        self.cancel_status = cancel_status
        self.cancel_error = cancel_error
        self.calls: list[tuple[Any, ...]] = []

    async def submit(self, prompt: str, **kwargs: Any) -> SubmitAccepted:
        self.calls.append(("submit", prompt, kwargs))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_result

    async def steer_task(
        self, task_id: str, prompt: str, **kwargs: Any
    ) -> TaskOperationResult:
        self.calls.append(("steer", task_id, prompt, kwargs))
        if self.steer_error is not None:
            raise self.steer_error
        return TaskOperationResult(task_id=task_id, status=self.steer_status)

    async def cancel_task(
        self, task_id: str, reason: str | None = None
    ) -> TaskOperationResult:
        self.calls.append(("cancel", task_id, reason))
        if self.cancel_error is not None:
            raise self.cancel_error
        return TaskOperationResult(task_id=task_id, status=self.cancel_status)


def make_service(
    client: FakeClient | None,
    *,
    state: ChatState | None = None,
) -> tuple[DispatchService, ChatState, list[str], list[tuple[str, str, str]]]:
    chat_state = state or ChatState(phase=RuntimePhase.READY)
    if not chat_state.pending:
        chat_state.enqueue("hello")
    statuses: list[str] = []
    notices: list[tuple[str, str, str]] = []
    service = DispatchService(
        chat_state,
        lambda: client,
        resource_root=None,
        on_status=statuses.append,
        on_notify=lambda text, title, severity: notices.append(
            (text, title, severity)
        ),
    )
    return service, chat_state, statuses, notices


def test_submit_accepted_clears_queue_and_returns_task() -> None:
    service, state, statuses, _ = make_service(FakeClient())

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.accepted_task_id == "task-1"
    assert outcome.schedule_next is True
    assert outcome.fatal_error is None
    assert state.pending_items == ()
    assert state.active_task_id == "task-1"
    assert "Accepted by runtime" in statuses


def test_steer_accepted_keeps_active_task() -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, statuses, _ = make_service(FakeClient(), state=state)

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.accepted_task_id is None
    assert outcome.schedule_next is True
    assert state.pending_items == ()
    assert state.active_task_id == "task-1"
    assert "Correction accepted for current task" in statuses


@pytest.mark.parametrize(
    "status", ["already_finished", "not_found", "not_running"]
)
def test_finished_steer_statuses_release_and_request_reconcile(
    status: str,
) -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, statuses, _ = make_service(
        FakeClient(steer_status=status), state=state
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.reconcile_task_id == "task-1"
    assert outcome.fatal_error is None
    assert state.pending_items == ("hello",)
    assert "Queued message will start the next task" in statuses


def test_already_cancelling_releases_without_fatal() -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, statuses, _ = make_service(
        FakeClient(steer_status="already_cancelling"), state=state
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_error is None
    assert state.pending_items == ("hello",)
    assert "Queued message is waiting for cancellation" in statuses


@pytest.mark.parametrize("status", ["invalid_content", "expired"])
def test_invalid_steer_statuses_block_with_notification(status: str) -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, chat_state, statuses, notices = make_service(
        FakeClient(steer_status=status), state=state
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_error is None
    assert outcome.refresh_queue is True
    assert chat_state.blocked_submission_id is not None
    assert statuses[-1] == f"Runtime rejected queued message: {status}"
    assert notices and notices[0][1] == "Message needs editing"


def test_unknown_steer_status_is_fatal() -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, _, _ = make_service(
        FakeClient(steer_status="unexpected"), state=state
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_stage == "steer response"
    assert isinstance(outcome.fatal_error, RuntimeError)


def test_transport_error_marks_outcome_unknown_and_requests_reconnect() -> None:
    service, state, _, _ = make_service(
        FakeClient(submit_error=GatewayTransportError("lost"))
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.reconnect_cause is not None
    assert outcome.fatal_error is None
    assert state.dispatch_reservation is not None
    assert state.dispatch_reservation.outcome_unknown is True


def test_transport_error_after_new_epoch_requests_retry() -> None:
    service, state, _, _ = make_service(
        FakeClient(submit_error=GatewayTransportError("lost"))
    )
    asyncio.run(service.dispatch_next(epoch=1))
    reservation = state.dispatch_reservation
    assert reservation is not None

    outcome = service.handle_result(
        reservation,
        epoch=2,
        error=GatewayTransportError("lost again"),
    )

    assert outcome.retry is not None
    assert outcome.retry.attempt_epoch == 2
    assert state.dispatch_reservation is outcome.retry


@pytest.mark.parametrize(
    "code",
    ["invalid_resource", "resource_unavailable", "invalid_content", "expired"],
)
def test_gateway_rejection_codes_block(code: str) -> None:
    service, state, statuses, notices = make_service(
        FakeClient(submit_error=GatewayClientError(code, "rejected"))
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_error is None
    assert state.blocked_submission_id is not None
    assert statuses[-1] == f"Runtime rejected queued message: {code}"
    assert notices and notices[0][1] == "Message needs editing"


def test_other_gateway_errors_are_fatal() -> None:
    service, _, _, _ = make_service(
        FakeClient(submit_error=GatewayClientError("internal", "boom"))
    )

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_stage == "message dispatch protocol"
    assert isinstance(outcome.fatal_error, GatewayClientError)


def test_missing_client_is_fatal() -> None:
    service, state, _, _ = make_service(None)

    outcome = asyncio.run(service.dispatch_next(epoch=1))

    assert outcome.fatal_stage == "message dispatch"
    assert isinstance(outcome.fatal_error, RuntimeError)
    assert state.phase == RuntimePhase.FAILED


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("accepted", "Cancellation requested"),
        ("already_cancelling", "Cancellation requested"),
        ("already_finished", "Task already finished"),
    ],
)
def test_cancel_statuses_update_status(status: str, message: str) -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, statuses, notices = make_service(
        FakeClient(cancel_status=status), state=state
    )

    outcome = asyncio.run(service.cancel_active(epoch=1))

    assert outcome.fatal_error is None
    assert message in statuses
    assert notices == []


def test_cancel_failure_notifies_without_fatal() -> None:
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    service, _, statuses, notices = make_service(
        FakeClient(cancel_error=RuntimeError("boom")), state=state
    )

    outcome = asyncio.run(service.cancel_active(epoch=1))

    assert outcome.fatal_error is None
    assert "Cancellation failed" in statuses
    assert notices and notices[0][1] == "Cancellation failed"
