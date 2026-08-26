"""Projection of public UserInputPlugin lifecycle events."""

from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.tui.src.event_pipeline.projectors.task_error import (
    project_task_error,
)
from apps.tui.src.event_pipeline.actions import (
    FinishTurn,
    SetRuntimeStatus,
    UiAction,
)


class UserInputProjector:
    """Convert input lifecycle Events without duplicating user messages."""

    def project(self, event: Event) -> tuple[UiAction, ...] | None:
        task_id = event.task_id
        if task_id is None:
            return ()

        if isinstance(event, InputQueuedEvent):
            return (
                SetRuntimeStatus(
                    task_id=task_id,
                    status="accepted",
                    text="Accepted by runtime",
                ),
            )

        if isinstance(event, InputStartedEvent):
            return (
                SetRuntimeStatus(
                    task_id=task_id,
                    status="running",
                    text="Agent is running",
                ),
            )

        if isinstance(event, InputFinishedEvent):
            return (FinishTurn(task_id=task_id, status=event.status),)

        if isinstance(event, UserInputEvent):
            return ()

        if isinstance(event, TaskErrorEvent):
            return project_task_error(event)

        return None
