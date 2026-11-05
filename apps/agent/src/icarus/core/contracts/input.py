"""UserInputPlugin 对外事件。"""

from dataclasses import dataclass, field
import json
from types import MappingProxyType
from typing import Mapping
from typing import Literal

from icarus.core.events import Event
from icarus.core.models.messages import ImagePart


@dataclass(frozen=True, kw_only=True)
class UserInputEvent(Event):
    prompt: str
    semantic_text: str | None = None
    input_images: list[ImagePart] = field(default_factory=list)
    request_options: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.semantic_text is not None and (
                not isinstance(self.semantic_text, str) or not self.semantic_text.strip() or len(self.semantic_text) > 8192):
            raise ValueError('semantic_text must contain 1..8192 characters')
        encoded = json.dumps(dict(self.request_options), ensure_ascii=False, allow_nan=False)
        if len(encoded) > 4096:
            raise ValueError("request_options exceeds 4096 characters")
        object.__setattr__(self, "request_options", MappingProxyType(json.loads(encoded)))


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
