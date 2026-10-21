"""Canonical observation values shared by sources and application services."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenSchema(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)


class ContentPart(FrozenSchema):
    kind: Literal['text', 'image', 'other']
    value: str


class ObservedItemDraft(FrozenSchema):
    source: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    deleted: bool = False
    is_self: bool = False
    strong_signal: bool = False
    sender_id: str
    sender_name: str | None = None
    sent_at: datetime
    observed_at: datetime
    content: tuple[ContentPart, ...] = ()
    locator: dict = Field(default_factory=dict)
    detail: dict = Field(default_factory=dict)

    @field_validator('sent_at', 'observed_at')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Observation times must be timezone aware')
        return value


class ObservedItem(ObservedItemDraft):
    id: UUID
    boot_id: UUID
    revision: int = Field(ge=1)
    fingerprint: str
    status: Literal['new', 'delivered', 'processed', 'verified', 'ignored', 'failed'] = 'new'
    status_reason: str | None = None
    decision_detail: dict | None = None
    wake_id: UUID | None = None


class SelectedItem(FrozenSchema):
    item: ObservedItem
    excerpt: str
    truncated: bool = False


class PendingWindow(FrozenSchema):
    first_observed_at: datetime
    last_observed_at: datetime
    has_strong_signal: bool


def item_text(item: ObservedItem | ObservedItemDraft) -> str:
    return '\n'.join(part.value if part.kind == 'text' else f'[{part.kind}]' for part in item.content)
