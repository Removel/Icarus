"""Platform-independent immutable observations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


class ObservationConflictError(ValueError):
    """A source identity was reused with different immutable facts."""


@dataclass(frozen=True, kw_only=True)
class SourceRef:
    platform: str
    account_id: str
    source_message_id: str
    occurred_at: datetime


@dataclass(frozen=True, kw_only=True)
class SubjectRef:
    platform: str
    kind: str
    external_id: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.kind}:{self.external_id}"


@dataclass(frozen=True, kw_only=True)
class ActorRef:
    external_id: str
    display_name: str | None


@dataclass(frozen=True, kw_only=True)
class ContentPart:
    kind: Literal["text", "image", "other"]
    value: str


@dataclass(frozen=True, kw_only=True)
class Observation:
    id: str
    source: SourceRef
    subject: SubjectRef
    actor: ActorRef
    content: tuple[ContentPart, ...]
    received_at: datetime
