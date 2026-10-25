"""Application-owned Argus JSON boundary values, independent of runtime and plugins."""

from hashlib import sha256
import json
from math import isfinite
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer


class HandoffError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class Scope(WireModel):
    source: Literal["qq"]
    account_id: str
    chat_kind: Literal["group", "private"]
    external_id: str

    @field_validator("account_id", "external_id")
    @classmethod
    def positive_identifier(cls, value):
        if not re.fullmatch(r"[1-9][0-9]{0,63}", value):
            raise ValueError("Invalid platform identifier")
        return value

    @property
    def key(self) -> str:
        return json.dumps([self.source, self.account_id, self.chat_kind, self.external_id], separators=(",", ":"))

    @property
    def chat_id(self) -> str:
        return f"{self.source}:{self.account_id}:{self.chat_kind}:{self.external_id}"


def uuid_text(value: str) -> str:
    return str(UUID(value))


class Locator(WireModel):
    account_id: str
    chat_kind: Literal["group", "private"]
    external_id: str
    message_id: str = Field(min_length=1, max_length=128)
    time: int | float

    @field_validator("time")
    @classmethod
    def finite_time(cls, value):
        if isinstance(value, bool) or not isfinite(value):
            raise ValueError("Invalid platform timestamp")
        return int(value) if value == int(value) else value


class WakeItem(WireModel):
    observation_id: str
    source: Literal["qq"]
    chat_id: str
    message_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    locator: Locator
    excerpt: str
    truncated: bool

    _id = field_validator("observation_id")(uuid_text)


class WakeRequest(WireModel):
    protocol_version: Literal[1, 2]
    target_session_id: str | None = None
    wake_id: str
    origin_boot_id: str
    scope: Scope
    reason: str = Field(min_length=1, max_length=1000)
    rule_id: str | None = Field(max_length=128)
    items: list[WakeItem] = Field(min_length=1, max_length=100)

    _ids = field_validator("wake_id", "origin_boot_id")(uuid_text)

    @model_validator(mode="before")
    @classmethod
    def versioned_target(cls, value):
        if isinstance(value, dict):
            version = value.get("protocol_version")
            if version == 2 and "target_session_id" not in value:
                raise ValueError("Version 2 requires target_session_id")
            if version == 1 and "target_session_id" in value:
                raise ValueError("Version 1 forbids target_session_id")
        return value

    @field_validator("target_session_id")
    @classmethod
    def target_uuid(cls, value):
        return uuid_text(value) if value is not None else None

    @model_serializer(mode="wrap")
    def versioned_wire(self, handler):
        value = handler(self)
        if self.protocol_version == 1:
            value.pop("target_session_id", None)
        return value

    @field_validator("protocol_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Unsupported protocol version")
        return value

    @field_validator("rule_id")
    @classmethod
    def nonempty_rule(cls, value):
        if value == "":
            raise ValueError("Rule identifier cannot be empty")
        return value

    @model_validator(mode="after")
    def matching_scope(self):
        identifiers = [item.observation_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate observation identifier")
        if sum(len(item.excerpt) for item in self.items) > 100000:
            raise ValueError("Wake text exceeds budget")
        for item in self.items:
            locator = item.locator
            if (item.chat_id != self.scope.chat_id or item.source != self.scope.source
                    or locator.account_id != self.scope.account_id
                    or locator.chat_kind != self.scope.chat_kind
                    or locator.external_id != self.scope.external_id
                    or locator.message_id != item.message_id):
                raise ValueError("Wake item does not match scope")
        return self

    @property
    def fingerprint(self) -> str:
        value = self.model_dump(mode="json")
        value["items"].sort(key=lambda item: item["observation_id"])
        return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
