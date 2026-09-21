"""Projection of public Task lifecycle RuntimeUpdate values."""

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import (
    AppendUserCorrection,
    AppendUserMessage,
    FinishTurn,
    SetRuntimeStatus,
    UiAction,
)


class UserInputProjector:
    def project(
        self, update: RuntimeUpdateModel, *, historical: bool = False
    ) -> tuple[UiAction, ...] | None:
        del historical
        task_id = update.task_id
        if task_id is None:
            return ()
        if update.type == "user.message":
            return (
                AppendUserMessage(
                    task_id=task_id,
                    text=str(update.payload.get("text", "")),
                ),
            )
        if update.type == "user.correction":
            step = update.payload.get("applied_before_step")
            if isinstance(step, bool) or not isinstance(step, int) or step < 1:
                raise ValueError(
                    "user.correction applied_before_step must be positive"
                )
            return (
                AppendUserCorrection(
                    task_id=task_id,
                    text=str(update.payload.get("text", "")),
                    applied_before_step=step,
                ),
            )
        if update.type == "task.accepted":
            return (SetRuntimeStatus(task_id, "accepted", "Accepted by runtime"),)
        if update.type == "task.started":
            return (SetRuntimeStatus(task_id, "running", "Agent is running"),)
        if update.type == "task.finished":
            status = str(update.payload["status"])
            if status not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
            }:
                return None
            return (FinishTurn(task_id=task_id, status=status),)  # type: ignore[arg-type]
        return None
