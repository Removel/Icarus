"""Projection of public BlackboardPlugin events."""

from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardCompactedEvent,
    BlackboardContextReadyEvent,
)
from apps.tui.src.event_pipeline.actions import UiAction
from apps.tui.src.event_pipeline.projectors.task_error import (
    project_task_error,
)


class BlackboardProjector:
    def project(self, event: Event) -> tuple[UiAction, ...] | None:
        if isinstance(event, TaskErrorEvent):
            return project_task_error(event)
        if isinstance(
            event,
            (BlackboardContextReadyEvent, BlackboardCompactedEvent),
        ):
            return ()
        return None
