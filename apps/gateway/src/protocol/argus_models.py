"""Strict service-only parameter models; application request validation stays public."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ServiceParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    caller_token: SecretStr
    protocol_version: Literal[1]

    @field_validator('protocol_version', mode='before')
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError('Invalid protocol version')
        return value


class WakeParams(ServiceParams):
    wake_id: str
    origin_boot_id: str
    scope: dict
    reason: str
    rule_id: str | None
    items: list[dict]


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
