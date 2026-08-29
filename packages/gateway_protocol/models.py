"""Wire models shared by Agent Gateway servers and clients."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from apps.agent.src.runtime_update import RuntimeUpdate


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceRefModel(StrictWireModel):
    resource_id: str = Field(min_length=1)
    media_type: str | None = None


class RuntimeUpdateModel(StrictWireModel):
    workspace_key: str
    session_id: str
    task_id: str | None
    type: str
    payload: dict[str, Any]
    occurred_at: datetime
    sequence: int | None = Field(default=None, ge=1)

    @classmethod
    def from_domain(cls, update: "RuntimeUpdate") -> "RuntimeUpdateModel":
        return cls(
            workspace_key=update.workspace_key,
            session_id=update.session_id,
            task_id=update.task_id,
            type=update.type,
            payload=dict(update.payload),
            occurred_at=update.occurred_at,
            sequence=update.sequence,
        )


class SessionHistoryModel(StrictWireModel):
    records: tuple[RuntimeUpdateModel, ...]
    history_cursor: int = Field(ge=0)
