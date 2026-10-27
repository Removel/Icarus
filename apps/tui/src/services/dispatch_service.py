"""Queue dispatch decisions without Textual."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from apps.tui.src.domain.chat_state import ChatState, DispatchReservation, RuntimePhase
from apps.tui.src.gateway_client import GatewayClientError, GatewayTransportError
from apps.tui.src.gateway_client.models import SubmitAccepted, TaskOperationResult
from apps.tui.src.gateway_client.ports import RuntimeClient
from apps.tui.src.domain.submission import (
    delete_submission_images,
    submission_resources,
)


@dataclass(frozen=True)
class DispatchOutcome:
    accepted_task_id: str | None = None
    reconcile_task_id: str | None = None
    refresh_queue: bool = True
    schedule_next: bool = False
    retry: DispatchReservation | None = None
    reconnect_cause: BaseException | None = None
    fatal_stage: str | None = None
    fatal_error: BaseException | None = None


class DispatchService:
    """Reserve, send, and settle one queued message."""

    def __init__(
        self,
        chat_state: ChatState,
        client: Callable[[], RuntimeClient | None],
        *,
        resource_root: Path | None,
        on_status: Callable[[str], None],
        on_notify: Callable[[str, str, str], None],
    ) -> None:
        self._chat_state = chat_state
        self._client = client
        self._resource_root = resource_root
        self._on_status = on_status
        self._on_notify = on_notify

    def bind_chat_state(self, chat_state: ChatState) -> None:
        self._chat_state = chat_state

    async def dispatch_next(self, *, epoch: int) -> DispatchOutcome:
        reservation = self._chat_state.begin_dispatch(epoch)
        if reservation is None:
            return DispatchOutcome(refresh_queue=False)
        result, error = await self.send_reservation(reservation)
        return self.handle_result(
            reservation,
            epoch=epoch,
            result=result,
            error=error,
        )

    async def send_reservation(
        self, reservation: DispatchReservation
    ) -> tuple[SubmitAccepted | TaskOperationResult | None, BaseException | None]:
        action = "Submitting queued message"
        if reservation.mode == "steer":
            action = "Adding queued message to current task"
        self._on_status(action)

        client = self._client()
        if client is None:
            return None, RuntimeError("Runtime is unavailable")

        message = reservation.message
        resources = submission_resources(message, self._resource_root)
        try:
            if reservation.mode == "submit":
                result = await client.submit(
                    prompt=message.model_prompt(),
                    submission_id=message.submission_id,
                    resources=resources,
                    display_text=message.text,
                )
            else:
                task_id = reservation.task_id
                if task_id is None:
                    raise RuntimeError(
                        "Steer reservation is missing its task ID"
                    )
                result = await client.steer_task(
                    task_id,
                    message.model_prompt(),
                    submission_id=message.submission_id,
                    resources=resources,
                    display_text=message.text,
                )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            return None, error
        return result, None

    def handle_result(
        self,
        reservation: DispatchReservation,
        *,
        epoch: int,
        result: SubmitAccepted | TaskOperationResult | None = None,
        error: BaseException | None = None,
    ) -> DispatchOutcome:
        if self._chat_state.dispatch_reservation is not reservation:
            return DispatchOutcome(refresh_queue=False)

        if isinstance(error, GatewayTransportError):
            unknown = self._chat_state.mark_dispatch_outcome_unknown(
                reservation
            )
            self._on_status("Connection lost; confirming queued message")
            if epoch > unknown.attempt_epoch:
                retry = self._chat_state.begin_dispatch_retry(unknown, epoch)
                return DispatchOutcome(retry=retry, refresh_queue=False)
            return DispatchOutcome(
                reconnect_cause=error,
                refresh_queue=False,
            )

        if isinstance(error, GatewayClientError):
            if error.code in {
                "invalid_resource",
                "resource_unavailable",
                "invalid_content",
                "expired",
            }:
                reason = f"Runtime rejected queued message: {error.code}"
                self._chat_state.block_dispatch(reservation, reason)
                self._on_status(reason)
                self._on_notify(
                    reason + " Press Ctrl+C to edit it.",
                    "Message needs editing",
                    "warning",
                )
                return DispatchOutcome()
            self._chat_state.release_dispatch(reservation, fatal=True)
            return DispatchOutcome(
                fatal_stage="message dispatch protocol",
                fatal_error=error,
                refresh_queue=False,
            )

        if error is not None:
            self._chat_state.release_dispatch(reservation, fatal=True)
            return DispatchOutcome(
                fatal_stage="message dispatch",
                fatal_error=error,
                refresh_queue=False,
            )

        if result is None:
            return DispatchOutcome(
                fatal_stage="message dispatch",
                fatal_error=RuntimeError("Dispatch result is missing"),
                refresh_queue=False,
            )

        if reservation.mode == "steer":
            return self._handle_steer_result(reservation, result)

        try:
            accepted_message = self._chat_state.accept_submit(
                reservation, result.task_id
            )
            delete_submission_images(accepted_message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return DispatchOutcome(
                fatal_stage="accepted message rendering",
                fatal_error=error,
                refresh_queue=False,
            )
        self._on_status("Accepted by runtime")
        return DispatchOutcome(
            accepted_task_id=result.task_id,
            schedule_next=True,
        )

    def _handle_steer_result(
        self,
        reservation: DispatchReservation,
        result: SubmitAccepted | TaskOperationResult,
    ) -> DispatchOutcome:
        status = result.status
        if status == "accepted":
            accepted_message = self._chat_state.accept_steer(reservation)
            delete_submission_images(accepted_message)
            self._on_status("Correction accepted for current task")
            return DispatchOutcome(schedule_next=True)
        if status in {"already_finished", "not_found", "not_running"}:
            self._chat_state.release_dispatch(reservation)
            self._on_status("Queued message will start the next task")
            return DispatchOutcome(
                reconcile_task_id=reservation.task_id,
                schedule_next=self._chat_state.phase == RuntimePhase.READY,
            )
        if status == "already_cancelling":
            self._chat_state.release_dispatch(reservation)
            self._on_status("Queued message is waiting for cancellation")
            return DispatchOutcome()
        if status in {"invalid_content", "expired"}:
            reason = f"Runtime rejected queued message: {status}"
            self._chat_state.block_dispatch(reservation, reason)
            self._on_status(reason)
            self._on_notify(
                reason + " Press Ctrl+C to edit it.",
                "Message needs editing",
                "warning",
            )
            return DispatchOutcome()
        self._chat_state.release_dispatch(reservation, fatal=True)
        return DispatchOutcome(
            fatal_stage="steer response",
            fatal_error=RuntimeError(f"Unknown status: {status}"),
            refresh_queue=False,
        )

    async def cancel_active(self, *, epoch: int) -> DispatchOutcome:
        del epoch
        task_id = self._chat_state.active_task_id
        client = self._client()
        if task_id is None or client is None:
            return DispatchOutcome(refresh_queue=False)
        try:
            result = await client.cancel_task(task_id, "user_requested")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._on_status("Cancellation failed")
            self._on_notify(
                f"Unable to cancel task: {type(error).__name__}: {error}",
                "Cancellation failed",
                "warning",
            )
            return DispatchOutcome(refresh_queue=False)
        if result.status in {"accepted", "already_cancelling"}:
            if self._chat_state.mark_cancelling(task_id):
                self._on_status("Cancellation requested")
            return DispatchOutcome(refresh_queue=False)
        if result.status == "already_finished":
            self._on_status("Task already finished")
            return DispatchOutcome(refresh_queue=False)
        self._on_notify(
            f"Unable to cancel task: {result.status}",
            "Cancellation failed",
            "warning",
        )
        return DispatchOutcome(refresh_queue=False)
