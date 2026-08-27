"""Pure interaction state for the Icarus Textual application."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from apps.tui.src.submission import DraftImage, PendingMessage


class RuntimePhase(str, Enum):
    """Lifecycle phase visible to the TUI."""

    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    CANCELLING = "cancelling"
    STOPPING = "stopping"
    FAILED = "failed"


class InterruptAction(str, Enum):
    """The single action selected for one ``Ctrl+C`` press."""

    CLEAR_DRAFT = "clear_draft"
    RESTORE_PENDING = "restore_pending"
    CANCEL_ACTIVE = "cancel_active"
    NOTIFY_CANCEL_UNAVAILABLE = "notify_cancel_unavailable"
    EXIT = "exit"


@dataclass
class ChatState:
    """Own local queue and one-active-task dispatch state.

    This state is deliberately independent from Textual and Agent event types so
    queue and interrupt behavior can be verified without starting either runtime.
    """

    phase: RuntimePhase = RuntimePhase.STARTING
    pending: deque[PendingMessage] = field(default_factory=deque)
    active_task_id: str | None = None
    dispatch_in_progress: bool = False

    @property
    def can_dispatch(self) -> bool:
        return (
            self.phase == RuntimePhase.READY
            and self.active_task_id is None
            and not self.dispatch_in_progress
            and bool(self.pending)
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
        self.dispatch_in_progress = False
        self.phase = RuntimePhase.FAILED

    def begin_stopping(self) -> None:
        self.dispatch_in_progress = False
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

    def begin_dispatch(self) -> PendingMessage | None:
        """Reserve the queue head while ``submit`` performs its handshake."""

        if not self.can_dispatch:
            return None
        self.dispatch_in_progress = True
        return self.pending[0]

    def accept_dispatch(self, task_id: str) -> PendingMessage:
        """Commit a successful submit and return the accepted user message."""

        if not self.dispatch_in_progress:
            raise RuntimeError("No dispatch is in progress")
        if not self.pending:
            raise RuntimeError("Pending queue changed during dispatch")
        if not task_id:
            raise ValueError("task_id cannot be empty")

        message = self.pending.popleft()
        self.dispatch_in_progress = False
        self.active_task_id = task_id
        self.phase = RuntimePhase.RUNNING
        return message

    def fail_dispatch(self) -> None:
        """Pause dispatch without losing the queue head."""

        if not self.dispatch_in_progress:
            return
        self.dispatch_in_progress = False
        self.phase = RuntimePhase.FAILED

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
        return self.pending.pop()

    def interrupt_action(self, draft: str | bool) -> InterruptAction:
        if draft:
            return InterruptAction.CLEAR_DRAFT
        if self.pending:
            return InterruptAction.RESTORE_PENDING
        if self.active_task_id is not None:
            return InterruptAction.CANCEL_ACTIVE
        if self.dispatch_in_progress:
            return InterruptAction.NOTIFY_CANCEL_UNAVAILABLE
        return InterruptAction.EXIT

    def mark_cancelling(self, task_id: str) -> bool:
        if self.active_task_id != task_id:
            return False
        self.phase = RuntimePhase.CANCELLING
        return True
