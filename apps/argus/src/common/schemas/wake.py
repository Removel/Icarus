"""External processing seam; acceptance is separate from completion."""

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from .observation import FrozenSchema

ACTIVE_STATUSES = ('waiting', 'sent', 'accepted')
TerminalStatus = Literal['resolved', 'no_action', 'failed']


class WakeItem(FrozenSchema):
    item_id: UUID
    source: str
    chat_id: str
    message_id: str
    revision: int = Field(ge=1)
    locator: dict
    excerpt: str
    truncated: bool = False


class WakePayload(FrozenSchema):
    wake_id: UUID
    boot_id: UUID
    chat_id: str
    reason: str
    rule_id: str | None = None
    items: tuple[WakeItem, ...]


class WakeReceipt(FrozenSchema):
    accepted: bool
    retryable: bool = False
    error_type: str | None = None


class WakeOutcome(FrozenSchema):
    wake_id: UUID
    item_ids: tuple[UUID, ...]
    status: TerminalStatus
    simulated: bool
    reason: str

    @field_validator('item_ids')
    @classmethod
    def unique_ids(cls, value):
        if not value or len(value) != len(set(value)):
            raise ValueError('Outcome IDs must be nonempty and unique')
        return value


class WakeView(FrozenSchema):
    id: UUID
    boot_id: UUID
    chat_id: str
    status: Literal['waiting', 'sent', 'accepted', 'resolved', 'no_action', 'failed']
    reason: str
    rule_id: str | None
    payload: WakePayload
    decision_detail: dict
    outcome: WakeOutcome | None
    attempt_count: int
    next_attempt_at: datetime | None
    accepted_at: datetime | None
    deadline_at: datetime | None
    error_type: str | None
    created_at: datetime
    updated_at: datetime


class AgentClient(Protocol):
    async def submit(self, payload: WakePayload) -> WakeReceipt: ...
    async def get_outcome(self, wake_id: UUID) -> WakeOutcome | None: ...
    async def release(self, wake_id: UUID) -> None: ...
    async def aclose(self) -> None: ...
