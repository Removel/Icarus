"""Strict application-owned HTTP request values for the Argus service boundary."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    protocol_version: Literal[1]

    @field_validator('protocol_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError('Invalid protocol version')
        return value


class BindingBody(RequestBody):
    binding_id: str

    @field_validator('binding_id')
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))


class Scope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    source: Literal['qq']
    account_id: str = Field(pattern=r'^[1-9][0-9]{0,63}$')
    chat_kind: Literal['group', 'private']
    external_id: str = Field(pattern=r'^[1-9][0-9]{0,63}$')


class BindBody(RequestBody):
    execution_id: str
    session_id: str
    task_id: str
    scope: Scope
    origin_boot_id: str
    request_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator('execution_id', 'session_id', 'task_id', 'origin_boot_id')
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))


class DeliverBody(BindingBody):
    observation_ids: list[str] = Field(min_length=1, max_length=100)


class ReadBody(BindingBody):
    observation_id: str

    @field_validator('observation_id')
    @classmethod
    def valid_observation(cls, value):
        return str(UUID(value))
    text_offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=20000, ge=1, le=20000)


class HistoryBody(BindingBody):
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=20, ge=1, le=50)


class WaitBody(HistoryBody):
    timeout_seconds: int | float = Field(default=30, ge=0, le=30, allow_inf_nan=False)


class ReportItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    observation_id: str
    status: Literal['handled', 'no_action']
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator('observation_id')
    @classmethod
    def valid_id(cls, value):
        return str(UUID(value))


class ReportBody(BindingBody):
    report_id: str = Field(min_length=1, max_length=128)
    items: list[ReportItem] = Field(min_length=1, max_length=50)

    @model_validator(mode='after')
    def unique_items(self):
        identifiers = [item.observation_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('Duplicate observation')
        return self


class FinalizeBody(BindingBody):
    execution_stopped: Literal[True]
    exit_kind: Literal['completed', 'failed', 'cancelled', 'timed_out', 'interrupted', 'start_failed']
    report_cursor: int = Field(ge=0)
