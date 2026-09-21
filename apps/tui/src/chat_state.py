"""Pure interaction state for the Icarus Textual application."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, TypeAlias

from apps.tui.src.submission import DraftImage, PendingMessage


class RuntimePhase(str, Enum):
    """Lifecycle phase visible to the TUI."""

    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SWITCHING = "switching"
    STOPPING = "stopping"
    FAILED = "failed"


class InterruptAction(str, Enum):
    """The single action selected for one ``Ctrl+C`` press."""

    CLEAR_DRAFT = "clear_draft"
    RESTORE_PENDING = "restore_pending"
    CANCEL_ACTIVE = "cancel_active"
    NOTIFY_CANCEL_UNAVAILABLE = "notify_cancel_unavailable"
    EXIT = "exit"


DispatchMode: TypeAlias = Literal["submit", "steer"]


@dataclass(frozen=True)
class DispatchReservation:
    message: PendingMessage
    mode: DispatchMode
    task_id: str | None
    attempt_epoch: int
    outcome_unknown: bool = False


@dataclass
class ChatState:
    """Own local queue and one-active-task dispatch state.

    This state is deliberately independent from Textual and Agent event types so
    queue and interrupt behavior can be verified without starting either runtime.
    """

    phase: RuntimePhase = RuntimePhase.STARTING
    pending: deque[PendingMessage] = field(default_factory=deque)
    active_task_id: str | None = None
    dispatch_reservation: DispatchReservation | None = None
    blocked_submission_id: str | None = None

    @property
    def dispatch_in_progress(self) -> bool:
        return self.dispatch_reservation is not None

    @property
    def can_dispatch(self) -> bool:
        return (
            (
                self.phase == RuntimePhase.READY
                and self.active_task_id is None
                or self.phase == RuntimePhase.RUNNING
                and self.active_task_id is not None
            )
            and self.dispatch_reservation is None
            and bool(self.pending)
            and self.pending[0].submission_id != self.blocked_submission_id
        )

    @property
    def can_run_session_command(self) -> bool:
        return (
            self.phase == RuntimePhase.READY
            and self.active_task_id is None
            and self.dispatch_reservation is None
            and not self.pending
        )

    @property
    def pending_items(self) -> tuple[str, ...]:
        """Return an immutable UI projection of the pending queue."""

        return tuple(item.text for item in self.pending)

    @property
    def pending_messages(self) -> tuple[PendingMessage, ...]:
        return tuple(self.pending)

    def mark_ready(self) -> None:
        if self.phase == RuntimePhase.STOPPING:
            raise RuntimeError("Cannot mark a stopping runtime ready")
        self.phase = (
            RuntimePhase.RUNNING
            if self.active_task_id is not None
            else RuntimePhase.READY
        )

    def mark_failed(self) -> None:
        self.dispatch_reservation = None
        self.phase = RuntimePhase.FAILED

    def begin_switching(self) -> None:
        if not self.can_run_session_command:
            raise RuntimeError("Session command requires an idle runtime")
        self.phase = RuntimePhase.SWITCHING

    def begin_stopping(self) -> None:
        self.dispatch_reservation = None
        self.phase = RuntimePhase.STOPPING

    def enqueue(
        self,
        message: str | PendingMessage,
        images: tuple[DraftImage, ...] = (),
    ) -> None:
        submission = (
            message
            if isinstance(message, PendingMessage)
            else PendingMessage(message, images)
        )
        if not submission.text.strip() and not submission.images:
            raise ValueError("Pending message cannot be empty")
        self.pending.append(submission)

    def begin_dispatch(
        self, connection_epoch: int
    ) -> DispatchReservation | None:
        """Reserve the queue head with its current submit/steer route."""

        if not self.can_dispatch:
            return None
        if connection_epoch < 0:
            raise ValueError("connection_epoch cannot be negative")
        mode: DispatchMode = (
            "steer" if self.active_task_id is not None else "submit"
        )
        reservation = DispatchReservation(
            message=self.pending[0],
            mode=mode,
            task_id=self.active_task_id,
            attempt_epoch=connection_epoch,
        )
        self.dispatch_reservation = reservation
        return reservation

    def accept_submit(
        self, reservation: DispatchReservation, task_id: str
    ) -> PendingMessage:
        """Commit a successful submit and return its pending message."""

        self._require_reservation(reservation, mode="submit")
        if not task_id:
            raise ValueError("task_id cannot be empty")
        message = self.pending.popleft()
        self.dispatch_reservation = None
        self.active_task_id = task_id
        self.phase = RuntimePhase.RUNNING
        return message

    def accept_steer(
        self, reservation: DispatchReservation
    ) -> PendingMessage:
        self._require_reservation(reservation, mode="steer")
        message = self.pending.popleft()
        self.dispatch_reservation = None
        self.phase = (
            RuntimePhase.RUNNING
            if self.active_task_id is not None
            else RuntimePhase.READY
        )
        return message

    def release_dispatch(
        self, reservation: DispatchReservation, *, fatal: bool = False
    ) -> None:
        self._require_reservation(reservation)
        self.dispatch_reservation = None
        if fatal:
            self.phase = RuntimePhase.FAILED

    def block_dispatch(
        self, reservation: DispatchReservation, reason: str
    ) -> None:
        if not reason.strip():
            raise ValueError("blocked dispatch reason cannot be empty")
        self._require_reservation(reservation)
        self.blocked_submission_id = reservation.message.submission_id
        self.dispatch_reservation = None

    def mark_dispatch_outcome_unknown(
        self, reservation: DispatchReservation
    ) -> DispatchReservation:
        self._require_reservation(reservation)
        updated = DispatchReservation(
            message=reservation.message,
            mode=reservation.mode,
            task_id=reservation.task_id,
            attempt_epoch=reservation.attempt_epoch,
            outcome_unknown=True,
        )
        self.dispatch_reservation = updated
        return updated

    def begin_dispatch_retry(
        self, reservation: DispatchReservation, connection_epoch: int
    ) -> DispatchReservation:
        self._require_reservation(reservation)
        if not reservation.outcome_unknown:
            raise RuntimeError("Dispatch outcome is not unknown")
        if connection_epoch <= reservation.attempt_epoch:
            raise RuntimeError("Dispatch retry requires a newer connection")
        updated = DispatchReservation(
            message=reservation.message,
            mode=reservation.mode,
            task_id=reservation.task_id,
            attempt_epoch=connection_epoch,
        )
        self.dispatch_reservation = updated
        return updated

    def finish_active(self, task_id: str) -> bool:
        """Finish only the currently active task."""

        if self.active_task_id != task_id:
            return False
        self.active_task_id = None
        if self.phase != RuntimePhase.STOPPING:
            self.phase = RuntimePhase.READY
        return True

    def pop_pending_tail(self) -> PendingMessage | None:
        if not self.pending:
            return None
        if (
            len(self.pending) == 1
            and self.dispatch_reservation is not None
            and self.pending[0] is self.dispatch_reservation.message
        ):
            return None
        message = self.pending.pop()
        if message.submission_id == self.blocked_submission_id:
            self.blocked_submission_id = None
        return message

    def interrupt_action(self, draft: str | bool) -> InterruptAction:
        if draft:
            return InterruptAction.CLEAR_DRAFT
        if self.pending:
            if self.pop_pending_tail_available:
                return InterruptAction.RESTORE_PENDING
            return InterruptAction.NOTIFY_CANCEL_UNAVAILABLE
        if self.active_task_id is not None:
            return InterruptAction.CANCEL_ACTIVE
        if self.dispatch_reservation is not None:
            return InterruptAction.NOTIFY_CANCEL_UNAVAILABLE
        return InterruptAction.EXIT

    def mark_cancelling(self, task_id: str) -> bool:
        if self.active_task_id != task_id:
            return False
        self.phase = RuntimePhase.CANCELLING
        return True

    @property
    def pop_pending_tail_available(self) -> bool:
        return bool(
            self.pending
            and not (
                len(self.pending) == 1
                and self.dispatch_reservation is not None
                and self.pending[0] is self.dispatch_reservation.message
            )
        )

    def _require_reservation(
        self,
        reservation: DispatchReservation,
        *,
        mode: DispatchMode | None = None,
    ) -> None:
        if self.dispatch_reservation is not reservation:
            raise RuntimeError("Dispatch reservation is stale")
        if mode is not None and reservation.mode != mode:
            raise RuntimeError(f"Expected a {mode} dispatch reservation")
        if not self.pending or self.pending[0] is not reservation.message:
            raise RuntimeError("Pending queue changed during dispatch")
