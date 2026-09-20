"""来源无关的 Task 运行中操作事件。"""

from dataclasses import dataclass
from datetime import datetime

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.run_control.types import (
    TaskOperationStatus,
)
from apps.agent.src.model_provider.types import ImagePart


@dataclass(frozen=True, kw_only=True)
class TaskContextInputEvent(Event):
    content: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include timezone information")


@dataclass(frozen=True, kw_only=True)
class TaskSteerRequestedEvent(Event):
    content: str
    input_images: tuple[ImagePart, ...] = ()
    display_text: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskSteerAppliedEvent(Event):
    request_event_id: str
    content: str
    input_images: tuple[ImagePart, ...] = ()
    display_text: str | None = None
    applied_before_step: int


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
