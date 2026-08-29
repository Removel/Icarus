"""Stable application updates published outside the Plugin EventBus."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from types import MappingProxyType
from typing import Literal, TypeAlias


RuntimeUpdateType: TypeAlias = Literal[
    "session.lifecycle",
    "user.message",
    "task.accepted",
    "task.started",
    "task.finished",
    "task.usage",
    "assistant.text_delta",
    "tool.started",
    "tool.completed",
    "task.error",
    "context.compacted",
]


@dataclass(frozen=True)
class RuntimeUpdate:
    workspace_key: str
    session_id: str
    task_id: str | None
    type: RuntimeUpdateType
    payload: Mapping[str, object]
    occurred_at: datetime
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.workspace_key:
            raise ValueError("workspace_key cannot be empty")
        if not self.session_id:
            raise ValueError("session_id cannot be empty")
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("sequence must be a positive integer")
        copied = dict(self.payload)
        try:
            serialized = json.dumps(
                copied, ensure_ascii=False, allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise TypeError("RuntimeUpdate payload must be JSON compatible") from error
        object.__setattr__(
            self,
            "payload",
            MappingProxyType(json.loads(serialized)),
        )
