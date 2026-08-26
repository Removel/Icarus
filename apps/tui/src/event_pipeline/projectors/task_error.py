"""Shared projection for TaskErrorEvent fields."""

from apps.agent.src.agent_orchestration.events import TaskErrorEvent
from apps.tui.src.event_pipeline.actions import AppendError, UiAction


def project_task_error(
    event: TaskErrorEvent,
    *,
    hidden_codes: frozenset[str] = frozenset(),
) -> tuple[UiAction, ...]:
    if event.task_id is None or event.code in hidden_codes:
        return ()
    return (
        AppendError(
            task_id=event.task_id,
            error_type=event.error_type,
            message=event.error_message,
        ),
    )
