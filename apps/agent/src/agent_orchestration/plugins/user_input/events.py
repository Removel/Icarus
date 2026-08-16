"""UserInputPlugin 对外事件。"""

from dataclasses import dataclass, field
from typing import Literal

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.model_provider.types import ImagePart, Message


@dataclass(frozen=True, kw_only=True)
class UserInputEvent(Event):
    history_messages: list[Message] = field(default_factory=list)
    prompt: str
    input_images: list[ImagePart] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class InputQueuedEvent(Event):
    task_id: str
    queue_position: int


@dataclass(frozen=True, kw_only=True)
class InputStartedEvent(Event):
    task_id: str


@dataclass(frozen=True, kw_only=True)
class InputFinishedEvent(Event):
    task_id: str
    status: Literal["completed", "failed"]
