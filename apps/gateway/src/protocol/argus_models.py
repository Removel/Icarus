"""Strict service-only parameter models; application request validation stays public."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from agent_app.application.models.scoped_execution import ScopedWakeRequest
from agent_app.argus_protocol import Scope


class ServiceParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    caller_token: SecretStr
    protocol_version: Literal[1, 2]

    @field_validator('protocol_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('Invalid protocol version')
        return value


class WakeParams(ScopedWakeRequest):
    """Reuse Agent's versioned target presence, normalization and frozen payload."""

    caller_token: SecretStr


class SessionsParams(ServiceParams):
    protocol_version: Literal[2]
    scope: Scope
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=10, ge=1, le=20)


class NotifyParams(ServiceParams):
    protocol_version: Literal[2]
    wake_id: str
    session_id: str

    @field_validator('wake_id', 'session_id')
    @classmethod
    def canonical_id(cls, value):
        return str(UUID(value))


class StatusParams(ServiceParams):
    wake_id: str | None = None
    scope: dict | None = None

    @model_validator(mode='after')
    def selector(self):
        if (self.wake_id is None) == (self.scope is None):
            raise ValueError('Exactly one execution selector is required')
        if self.wake_id is not None:
            self.wake_id = str(UUID(self.wake_id))
        return self


class CancelParams(ServiceParams):
    wake_id: str
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('wake_id')
    @classmethod
    def canonical_id(cls, value):
        return str(UUID(value))


class ReleaseParams(ServiceParams):
    wake_id: str

    @field_validator('wake_id')
    @classmethod
    def canonical_id(cls, value):
        return str(UUID(value))
