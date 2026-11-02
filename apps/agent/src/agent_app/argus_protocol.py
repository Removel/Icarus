"""Frozen, strict Argus bridge HTTP DTOs (no runtime/framework dependency)."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128)]

class WireModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class SessionRequest(WireModel):
    channel_id: Id
    request_id: Id

class Observation(WireModel):
    id: Id
    source_key: Annotated[str, Field(min_length=1, max_length=512)]
    message_id: Id
    kind: Literal['message.observed']
    revision: Annotated[int, Field(ge=1)]
    observed_at: Annotated[str, Field(min_length=1, max_length=64)]
    content: dict
    detail: dict

    @field_validator('observed_at')
    @classmethod
    def utc_timestamp(cls, value):
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError('observed_at must be an explicit UTC timestamp')
        return value

    @model_validator(mode='after')
    def json_content(self):
        json.dumps(self.content, allow_nan=False)
        if len(json.dumps(self.detail, allow_nan=False).encode()) > 48 * 1024:
            raise ValueError('observation detail exceeds 48 KiB')
        return self

class ChannelSnapshot(WireModel):
    id: Id
    title: Annotated[str, Field(max_length=120)]
    goal: Annotated[str, Field(max_length=4096)]
    progress: Annotated[str, Field(max_length=8192)]
    lifecycle: Literal['active', 'paused', 'completed']
    version: Annotated[int, Field(ge=1)]
    source_keys: Annotated[list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=16)]
    rule_source: str

    @field_validator('rule_source')
    @classmethod
    def source_size(cls, value):
        if len(value.encode()) > 32768:
            raise ValueError('Rule source exceeds 32 KiB')
        return value


class WakeRequest(WireModel):
    channel_id: Id
    wake_id: Id
    binding_id: Id
    channel_version: Annotated[int, Field(ge=1)]
    channel_snapshot: ChannelSnapshot
    rule_sdk: dict
    session_id: Id
    items: Annotated[list[Observation], Field(min_length=1, max_length=20)]
    argus_url: Annotated[str, Field(max_length=2048)]

    @field_validator('argus_url')
    @classmethod
    def endpoint(cls, value):
        url = urlsplit(value)
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('argus_url must be an absolute HTTP endpoint without credentials/query/fragment')
        return value.rstrip('/')

    @model_validator(mode='after')
    def bounded(self):
        if self.channel_snapshot.id != self.channel_id or self.channel_snapshot.version != self.channel_version:
            raise ValueError('Channel snapshot identity/version mismatch')
        if len(json.dumps(self.rule_sdk, allow_nan=False).encode()) > 32768:
            raise ValueError('Rule SDK metadata exceeds 32 KiB')
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError('duplicate observation IDs')
        if len(json.dumps(self.model_dump()).encode()) > 512 * 1024:
            raise ValueError('wake exceeds 512 KiB')
        return self

class StatusRequest(WireModel):
    wake_id: Id
