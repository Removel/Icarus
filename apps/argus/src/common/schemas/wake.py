"""External processing seam; acceptance is separate from completion."""

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator, model_serializer, model_validator

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
    protocol_version: Literal[1, 2] = 1
    target_session_id: UUID | None = None

    @model_validator(mode='before')
    @classmethod
    def require_target(cls, value):
        if isinstance(value, dict):
            version = value.get('protocol_version', 1)
            if type(version) is not int or version not in (1, 2):
                raise ValueError('Invalid protocol version')
            if version == 2 and 'target_session_id' not in value:
                raise ValueError('v2 target_session_id is required nullable')
            if version == 1 and 'target_session_id' in value:
                raise ValueError('v1 cannot target a semantic session')
        return value

    @model_serializer(mode='wrap')
    def legacy_dump(self, handler):
        value = handler(self)
        if self.protocol_version == 1:
            value.pop('protocol_version', None)
            value.pop('target_session_id', None)
        return value

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
    handoff: dict = Field(default_factory=lambda: {'mode': 'mock'})
    attempt_count: int
    next_attempt_at: datetime | None
    accepted_at: datetime | None
    deadline_at: datetime | None
    error_type: str | None
    created_at: datetime
    updated_at: datetime


def wake_wire(payload):
    source, account, kind, target = payload.chat_id.split(':', 3)
    scope = {'source': source, 'account_id': account, 'chat_kind': kind, 'external_id': target}
    return {'protocol_version': payload.protocol_version,
            **({'target_session_id': str(payload.target_session_id) if payload.target_session_id else None}
               if payload.protocol_version == 2 else {}),
            'wake_id': str(payload.wake_id), 'origin_boot_id': str(payload.boot_id),
            'scope': scope, 'reason': payload.reason, 'rule_id': payload.rule_id,
            'items': [{'observation_id': str(item.item_id), 'source': item.source, 'chat_id': item.chat_id,
                'message_id': item.message_id, 'revision': item.revision, 'locator': dict(item.locator),
                'excerpt': item.excerpt, 'truncated': item.truncated} for item in payload.items]}


def request_fingerprint(payload):
    value = wake_wire(payload)
    value['items'].sort(key=lambda item: item['observation_id'])
    for item in value['items']:
        stamp = item['locator'].get('time')
        if isinstance(stamp, float) and stamp.is_integer():
            item['locator']['time'] = int(stamp)
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class AgentClient(Protocol):
    async def submit(self, payload: WakePayload) -> WakeReceipt: ...
    async def get_outcome(self, wake_id: UUID) -> WakeOutcome | None: ...
    async def release(self, wake_id: UUID) -> None: ...
    async def aclose(self) -> None: ...
