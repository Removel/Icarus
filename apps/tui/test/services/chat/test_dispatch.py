"""Exercise dispatch rules at the same serial completion seam used by ChatSession."""

import pytest

from apps.tui.src.domain.chat_state import ChatState, RuntimePhase
from apps.tui.src.client.gateway.models import SubmitAccepted, TaskOperationResult
from apps.tui.src.client.gateway import GatewayClientError, GatewayTransportError
from apps.tui.src.services.chat.dispatch import settle_dispatch


def reserve(*, steer=False):
    state = ChatState(
        phase=RuntimePhase.RUNNING if steer else RuntimePhase.READY,
        active_task_id="t" if steer else None,
    )
    state.enqueue("hello")
    return state, state.begin_dispatch(1)


def test_submit_accepted_clears_queue_and_returns_task():
    state, reservation = reserve()
    result = settle_dispatch(
        state, reservation, epoch=1, value=SubmitAccepted("new", 0)
    )
    assert result.accepted.text == "hello"
    assert state.active_task_id == "new"
    assert not state.pending


def test_steer_accepted_keeps_active_task():
    state, reservation = reserve(steer=True)
    settle_dispatch(
        state, reservation, epoch=1, value=TaskOperationResult("t", "accepted")
    )
    assert state.active_task_id == "t" and not state.pending


@pytest.mark.parametrize(
    "status", ["already_finished", "not_found", "not_running", "already_cancelling"]
)
def test_finished_steer_statuses_release_and_request_reconcile(status):
    state, reservation = reserve(steer=True)
    result = settle_dispatch(
        state, reservation, epoch=1, value=TaskOperationResult("t", status)
    )
    assert result.reconcile == "t"
    assert state.pending_items == ("hello",) and state.dispatch_reservation is None


@pytest.mark.parametrize("status", ["invalid_content", "expired"])
def test_invalid_steer_statuses_block(status):
    state, reservation = reserve(steer=True)
    result = settle_dispatch(
        state, reservation, epoch=1, value=TaskOperationResult("t", status)
    )
    assert result.blocked == status
    assert state.blocked_submission_id == reservation.message.submission_id


def test_unknown_steer_status_is_fatal():
    state, reservation = reserve(steer=True)
    with pytest.raises(RuntimeError, match="Unknown steer"):
        settle_dispatch(
            state, reservation, epoch=1, value=TaskOperationResult("t", "unexpected")
        )


def test_transport_error_marks_outcome_unknown_and_requests_reconnect():
    state, reservation = reserve()
    result = settle_dispatch(
        state, reservation, epoch=1, error=GatewayTransportError("lost")
    )
    assert result.reconnect is not None
    assert state.dispatch_reservation.outcome_unknown


def test_transport_error_after_new_epoch_requests_retry():
    state, reservation = reserve()
    result = settle_dispatch(
        state, reservation, epoch=2, error=GatewayTransportError("lost")
    )
    assert result.retry.attempt_epoch == 2
    assert result.retry.message.submission_id == reservation.message.submission_id


@pytest.mark.parametrize(
    "code", ["invalid_resource", "resource_unavailable", "invalid_content", "expired"]
)
def test_gateway_rejection_codes_block(code):
    state, reservation = reserve()
    result = settle_dispatch(
        state, reservation, epoch=1, error=GatewayClientError(code, "rejected")
    )
    assert result.blocked == code
    assert state.blocked_submission_id


def test_other_gateway_errors_propagate():
    state, reservation = reserve()
    with pytest.raises(GatewayClientError):
        settle_dispatch(
            state, reservation, epoch=1, error=GatewayClientError("internal", "boom")
        )


def test_missing_result_is_fatal():
    state, reservation = reserve()
    with pytest.raises(RuntimeError, match="missing"):
        settle_dispatch(state, reservation, epoch=1)
