"""Boundary facts shared by protocols, rules and persistence."""
from typing import Any, Literal
from uuid import UUID
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("id", check_fields=False)
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        UUID(value)
        return value


class Observation(Fact):
    id: str
    source_key: str
    message_id: str
    kind: str
    revision: int = Field(ge=1)
    observed_at: str
    content: dict[str, Any]
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def utc_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("observed_at must be a UTC ISO timestamp")
        return value


class Channel(Fact):
    id: str
    title: str
    goal: str
    progress: str = ""
    lifecycle: Literal["active", "paused", "completed"] = "active"
    version: int = Field(default=1, ge=1)
    subscriptions: dict[str, str | None] = Field(default_factory=dict)
    rule_path: str = ""
    rule_hash: str = ""
    runtime: dict[str, Any] = Field(default_factory=dict)


class DiscoveryDecision(Fact):
    action: Literal['ignore', 'existing', 'new', 'defer']
    reason: str
    channel_id: str | None = None

    @model_validator(mode='after')
    def route_identity(self):
        if (self.action == 'existing') != (self.channel_id is not None):
            raise ValueError('Only existing decisions require channel_id')
        return self


class Decision(Fact):
    passed: bool
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    defer: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.passed and self.defer:
            raise ValueError("passed and defer cannot both be true")
        return self
