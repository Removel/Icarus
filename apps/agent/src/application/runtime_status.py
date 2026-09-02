"""Read-only application runtime status projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias


SessionLifecycle: TypeAlias = Literal[
    "loading",
    "ready",
    "running",
    "unloading",
    "unloaded",
    "failed",
]


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


@dataclass(frozen=True)
class SessionStatus:
    workspace_key: str
    session_id: str
    lifecycle: SessionLifecycle
    active_task_ids: tuple[str, ...] = ()
    queued_task_count: int = 0
    pending_event_count: int = 0
    pending_plugin_event_count: int = 0
    background_work_count: int = 0
    last_activity_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    first_user_input: str


DiscardSessionStatus: TypeAlias = Literal[
    "discarded",
    "not_empty",
    "busy",
    "not_found",
]


@dataclass(frozen=True)
class DiscardSessionResult:
    workspace_key: str
    session_id: str
    status: DiscardSessionStatus


@dataclass(frozen=True)
class UnloadResult:
    workspace_key: str
    session_id: str
    status: Literal["unloaded", "already_unloaded", "busy", "not_found"]


TaskLifecycle: TypeAlias = Literal[
    "accepted",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


@dataclass(frozen=True)
class TaskStatus:
    workspace_key: str
    session_id: str
    task_id: str
    lifecycle: TaskLifecycle
    queue_position: int | None = None
    run_id: str | None = None
    error_code: str | None = None
    updated_at: datetime | None = None
