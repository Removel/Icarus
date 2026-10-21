"""Explicit Gateway response models keep domain implementation details off the wire."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class DomainWireModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SessionStatusModel(DomainWireModel):
    workspace_key: str
    session_id: str
    lifecycle: Literal["loading", "ready", "running", "unloading", "unloaded", "failed"]
    active_task_ids: tuple[str, ...] = ()
    queued_task_count: int = 0
    pending_event_count: int = 0
    pending_plugin_event_count: int = 0
    background_work_count: int = 0
    last_activity_at: datetime | None = None
    error: str | None = None


class TaskStatusModel(DomainWireModel):
    workspace_key: str
    session_id: str
    task_id: str
    lifecycle: str
    updated_at: datetime
    queue_position: int | None = None
    run_id: str | None = None
    error_code: str | None = None


class UnloadResultModel(DomainWireModel):
    workspace_key: str
    session_id: str
    status: Literal["unloaded", "already_unloaded", "busy", "not_found"]
