"""Small canonical data builders for functional Argus tests."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from apps.argus.src.common.schemas.observation import ContentPart, ObservedItem, ObservedItemDraft
from apps.argus.src.common.schemas.wake import WakeItem, WakePayload


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 21, tzinfo=UTC)

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def draft_factory(text='hello', **changes):
    now = datetime.now(UTC)
    return ObservedItemDraft(**{'source': 'qq', 'chat_id': 'qq:123:group:456',
        'message_id': str(uuid4()), 'sender_id': '777', 'sender_name': 'peer',
        'sent_at': now, 'observed_at': now, 'content': (ContentPart(kind='text', value=text),),
        'locator': {'chat_kind': 'group'}, **changes})


def item_factory(text='hello', **changes):
    facts = {key: changes.pop(key) for key in tuple(changes) if key in ObservedItemDraft.model_fields}
    draft = draft_factory(text, **facts)
    return ObservedItem(**draft.model_dump(), id=uuid4(), boot_id=uuid4(), revision=1,
                        fingerprint='test', **changes)


def payload_factory():
    return WakePayload(wake_id=uuid4(), boot_id=uuid4(), chat_id='qq:123:group:456', reason='test',
        items=(WakeItem(item_id=uuid4(), source='qq', chat_id='qq:123:group:456', message_id='789',
                        revision=1, locator={}, excerpt='hello'),))
