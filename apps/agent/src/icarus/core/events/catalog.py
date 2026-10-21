"""Stable business-event type identifiers; instance IDs remain unrelated UUIDs."""

from icarus.core.events.base_event import Event
from icarus.core.events.task_error import TaskErrorEvent
from icarus.core.agents import types as agents
from icarus.core.contracts import context, input, process
from icarus.core.run_control import events as control


class EventCatalog:
    def __init__(self) -> None:
        self._types: dict[str, type[Event]] = {}

    def register(self, event_id: str, event_type: type[Event]) -> None:
        if event_id in self._types or event_type in self._types.values():
            raise ValueError(f"Duplicate event contract: {event_id}")
        if not issubclass(event_type, Event):
            raise TypeError("Event catalog entries must be Event types")
        self._types[event_id] = event_type

    def copy(self) -> "EventCatalog":
        catalog = EventCatalog()
        catalog._types.update(self._types)
        return catalog

    def resolve(self, event_id: str) -> type[Event]:
        return self._types[event_id]

    def id_for(self, event_type: type[Event]) -> str:
        for key, value in self._types.items():
            if value is event_type:
                return key
        raise KeyError(event_type)


def builtin_event_catalog() -> EventCatalog:
    catalog = EventCatalog()
    definitions = {
        "agent.cancelled": agents.AgentCancelledEvent,
        "agent.completed": agents.AgentCompletedEvent,
        "agent.message_completed": agents.AgentMessageCompletedEvent,
        "agent.text_delta": agents.AgentTextDeltaEvent,
        "agent.thinking_completed": agents.AgentThinkingCompletedEvent,
        "agent.thinking_delta": agents.AgentThinkingDeltaEvent,
        "agent.tool_completed": agents.AgentToolCompletedEvent,
        "agent.tool_started": agents.AgentToolStartedEvent,
        "task.error": TaskErrorEvent,
        "context.compacted": context.BlackboardCompactedEvent,
        "context.ready": context.BlackboardContextReadyEvent,
        "context.region_updated": context.BlackboardRegionUpdatedEvent,
        "context.contribution": context.ContextContributionEvent,
        "process.updated": process.ProcessUpdatedEvent,
        "input.finished": input.InputFinishedEvent,
        "input.queued": input.InputQueuedEvent,
        "input.started": input.InputStartedEvent,
        "input.user_input": input.UserInputEvent,
        "task.cancel_requested": control.TaskCancelRequestedEvent,
        "task.cancel_result": control.TaskCancelResultEvent,
        "task.context_input": control.TaskContextInputEvent,
        "task.context_input_result": control.TaskContextInputResultEvent,
        "task.steer_applied": control.TaskSteerAppliedEvent,
        "task.steer_requested": control.TaskSteerRequestedEvent,
    }
    for key, value in definitions.items():
        catalog.register(key, value)
    return catalog
