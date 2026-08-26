"""Projection of public AgentPlugin stream events."""

import json

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.tui.src.event_pipeline.actions import (
    AppendAssistantDelta,
    AppendToolStarted,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.event_pipeline.projectors.task_error import (
    project_task_error,
)


class AgentProjector:
    """Convert Agent output without exposing full results or reasoning."""

    def project(self, event: Event) -> tuple[UiAction, ...] | None:
        task_id = event.task_id
        if task_id is None:
            return ()

        if isinstance(event, AgentTextDeltaEvent):
            if not event.text:
                return ()
            return (AppendAssistantDelta(task_id=task_id, text=event.text),)

        if isinstance(event, AgentToolStartedEvent):
            arguments_json = json.dumps(
                event.tool_call.arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            return (
                AppendToolStarted(
                    task_id=task_id,
                    call_id=event.tool_call.id,
                    tool_name=event.tool_call.name,
                    arguments_json=arguments_json,
                ),
            )

        if isinstance(event, AgentToolCompletedEvent):
            return (
                UpdateToolCompleted(
                    task_id=task_id,
                    call_id=event.tool_call.id,
                    tool_name=event.tool_call.name,
                    success=event.result.success,
                    error=(
                        event.result.error
                        if not event.result.success
                        else None
                    ),
                ),
            )

        if isinstance(event, TaskErrorEvent):
            return project_task_error(
                event,
                hidden_codes=frozenset({"tool_execution_failed"}),
            )

        if isinstance(event, AgentCompletedEvent):
            return ()

        return None
