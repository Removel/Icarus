"""来源无关的 Task 运行中操作事件。"""

from dataclasses import dataclass

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.run_control.types import (
    TaskOperationStatus,
)


@dataclass(frozen=True, kw_only=True)
class TaskContextInputEvent(Event):
    content: str


@dataclass(frozen=True, kw_only=True)
class TaskCancelRequestedEvent(Event):
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskContextInputResultEvent(Event):
    request_event_id: str
    status: TaskOperationStatus
    run_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskCancelResultEvent(Event):
    request_event_id: str
    status: TaskOperationStatus
    run_id: str | None = None
