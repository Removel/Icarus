"""Projection of public UserInputPlugin lifecycle events."""

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.tui.src.event_pipeline.actions import (
    FinishTurn,
    SetRuntimeStatus,
    UiAction,
)


class UserInputProjector:
    """Convert input lifecycle Events without duplicating user messages."""

    def project(self, event: Event) -> tuple[UiAction, ...] | None:
        task_id = event.correlation_id
        if task_id is None:
            return ()

        if isinstance(event, InputQueuedEvent):
            if event.task_id != task_id:
                return ()
            return (
                SetRuntimeStatus(
                    task_id=task_id,
                    status="accepted",
                    text="Accepted by runtime",
                ),
            )

        if isinstance(event, InputStartedEvent):
            if event.task_id != task_id:
                return ()
            return (
                SetRuntimeStatus(
                    task_id=task_id,
                    status="running",
                    text="Agent is running",
                ),
            )

        if isinstance(event, InputFinishedEvent):
            if event.task_id != task_id:
                return ()
            return (FinishTurn(task_id=task_id, status=event.status),)

        if isinstance(event, UserInputEvent):
            return ()

        return None
