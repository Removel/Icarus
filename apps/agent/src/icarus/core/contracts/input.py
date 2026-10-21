"""UserInputPlugin 对外事件。"""

from dataclasses import dataclass, field
from typing import Literal

from icarus.core.events import Event
from icarus.core.models.messages import ImagePart


@dataclass(frozen=True, kw_only=True)
class UserInputEvent(Event):
    prompt: str
    input_images: list[ImagePart] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class InputQueuedEvent(Event):
    queue_position: int


@dataclass(frozen=True, kw_only=True)
class InputStartedEvent(Event):
    pass


@dataclass(frozen=True, kw_only=True)
class InputFinishedEvent(Event):
    status: Literal["completed", "failed", "cancelled"]
    run_id: str | None = None


@dataclass(frozen=True)
class InputAccepted:
    task_id: str
    queue_position: int
