"""Read-only generic session work snapshot."""
from dataclasses import dataclass, field
from datetime import datetime
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class SessionContext:
    kind: str = "interactive"
    scope: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "scope", _freeze(self.scope))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

@dataclass(frozen=True)
class SessionRuntimeSnapshot:
    active_task_ids: tuple[str, ...]
    queued_task_count: int
    pending_event_count: int
    pending_plugin_event_count: int
    background_work_count: int
    last_event_at: datetime | None
    last_background_work_at: datetime | None

    @property
    def has_work(self) -> bool:
        return bool(
            self.active_task_ids
            or self.queued_task_count
            or self.pending_event_count
            or self.pending_plugin_event_count
            or self.background_work_count
        )

