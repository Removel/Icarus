"""Wire models shared by Agent Gateway servers and clients."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from apps.agent.src.runtime_update import RuntimeUpdate
    from apps.agent.src.application.runtime_status import (
        DiscardSessionResult,
        SessionSummary,
    )


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


class SessionSummaryModel(StrictWireModel):
    session_id: str
    first_user_input: str

    @classmethod
    def from_domain(cls, summary: "SessionSummary") -> "SessionSummaryModel":
        return cls(
            session_id=summary.session_id,
            first_user_input=summary.first_user_input,
        )


class SessionListModel(StrictWireModel):
    sessions: tuple[SessionSummaryModel, ...]


class DiscardEmptySessionResultModel(StrictWireModel):
    workspace_key: str
    session_id: str
    status: Literal["discarded", "not_empty", "busy", "not_found"]

    @classmethod
    def from_domain(
        cls, result: "DiscardSessionResult"
    ) -> "DiscardEmptySessionResultModel":
        return cls(
            workspace_key=result.workspace_key,
            session_id=result.session_id,
            status=result.status,
        )
