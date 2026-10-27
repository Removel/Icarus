"""Dispatch response classification, independent of I/O and terminal rendering."""

from dataclasses import dataclass

from apps.tui.src.client.gateway import GatewayClientError, GatewayTransportError
from apps.tui.src.domain.chat_state import ChatState, DispatchReservation


@dataclass(frozen=True)
class DispatchDecision:
    accepted: object = None
    reconcile: str | None = None
    reconnect: Exception | None = None
    retry: DispatchReservation | None = None
    blocked: str = ""


def settle_dispatch(
    state: ChatState,
    reservation: DispatchReservation,
    *,
    epoch: int,
    value=None,
    error=None,
) -> DispatchDecision:
    """Settle the exact reserved request; callers have already checked client identity."""
    if state.dispatch_reservation is not reservation:
        return DispatchDecision()
    if isinstance(error, GatewayTransportError):
        unknown = state.mark_dispatch_outcome_unknown(reservation)
        if epoch > unknown.attempt_epoch:
            return DispatchDecision(retry=state.begin_dispatch_retry(unknown, epoch))
        return DispatchDecision(reconnect=error)
    if isinstance(error, GatewayClientError) and error.code in {
        "invalid_resource",
        "resource_unavailable",
        "invalid_content",
        "expired",
    }:
        state.block_dispatch(reservation, str(error))
        return DispatchDecision(blocked=error.code)
    if error is not None:
        raise error
    if value is None:
        raise RuntimeError("Dispatch result is missing")
    if reservation.mode == "submit":
        return DispatchDecision(
            accepted=state.accept_submit(reservation, value.task_id)
        )
    if value.status == "accepted":
        return DispatchDecision(accepted=state.accept_steer(reservation))
    if value.status in {
        "already_finished",
        "not_found",
        "not_running",
        "already_cancelling",
    }:
        state.release_dispatch(reservation)
        return DispatchDecision(reconcile=reservation.task_id)
    if value.status in {"invalid_content", "expired"}:
        state.block_dispatch(reservation, value.status)
        return DispatchDecision(blocked=value.status)
    raise RuntimeError(f"Unknown steer status: {value.status}")
