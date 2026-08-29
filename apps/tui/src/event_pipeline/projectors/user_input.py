"""Projection of public Task lifecycle RuntimeUpdate values."""

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.event_pipeline.actions import (
    AppendUserMessage,
    FinishTurn,
    SetRuntimeStatus,
    UiAction,
)


class UserInputProjector:
    def project(
        self, update: RuntimeUpdateModel
    ) -> tuple[UiAction, ...] | None:
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
