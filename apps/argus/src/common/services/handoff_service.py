"""Scope-fixed safe observation output, cursors and cancellable long polling."""

import asyncio
import base64
from datetime import datetime
import json
from uuid import UUID

from ..schemas.observation import item_text
from ..repositories.handoff_repository import HandoffConflict
from .observation_access_policy import ObservationAccessPolicy
from .observation_notifier import ObservationNotifier


class HandoffService:
    def __init__(self, repository, *, policy=None, notifier=None):
        self.repository = repository
        self.policy = policy or ObservationAccessPolicy()
        self.notifier = notifier or ObservationNotifier()

    @staticmethod
    def cursor(scope, direction, stamp, identifier):
        value = {'scope': scope, 'direction': direction, 'observed_at': stamp.isoformat(), 'id': str(identifier)}
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode()

    @staticmethod
    def decode_cursor(cursor, scope, direction):
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor))
            if value['scope'] != scope or value['direction'] != direction:
                raise ValueError()
            stamp = datetime.fromisoformat(value['observed_at'])
            if stamp.tzinfo is None:
                raise ValueError()
            return stamp, UUID(value['id'])
        except (ValueError, KeyError, TypeError):
            raise HandoffConflict('invalid_cursor') from None

    @staticmethod
    def item(value, role, origin, latest, *, excerpt=''):
        return {'observation_id': str(value.id), 'source': value.source, 'chat_id': value.chat_id,
                'message_id': value.message_id, 'revision': value.revision, 'locator': value.locator,
                'observed_at': value.observed_at.isoformat(), 'deleted': value.deleted, 'latest': latest,
                'role': role, 'origin': origin, 'excerpt': excerpt, 'truncated': len(excerpt) < len(item_text(value))}

    async def bind(self, wake_id, payload):
        binding = await self.repository.bind(wake_id, payload)
        references = []
        async with self.repository.database.session() as db:
            wake, _ = await self.repository._binding(db, binding['binding_id'], lock=False)
            frozen = {str(value.get('observation_id', value.get('item_id'))): value for value in wake.payload['items']}
        for identifier, initial in frozen.items():
            row, role, origin, latest = await self.repository.read(binding['binding_id'], identifier)
            if not self.policy.allows(row):
                references.append({'observation_id': identifier, 'role': 'context', 'origin': 'initial', 'content_restricted': True})
            else:
                references.append(self.item(row, role, origin, latest, excerpt=initial['excerpt']))
        scope = binding['scope']
        watermark = binding['initial_cursor']
        cursor = self.cursor(scope, 'forward', datetime.fromisoformat(watermark['observed_at']), watermark['id'])
        return {**binding, 'protocol_version': 1, 'initial_items': references, 'initial_cursor': cursor}

    async def deliver(self, binding_id, identifiers):
        for identifier in identifiers:
            item, _, origin, _ = await self.repository.read(binding_id, identifier)
            if origin != 'initial':
                raise HandoffConflict('item_not_initial')
            if not self.policy.allows(item):
                raise HandoffConflict('content_restricted')
        await self.repository.mark_exposed(binding_id, identifiers)
        return {'protocol_version': 1, 'delivered_ids': list(map(str, identifiers))}

    async def read(self, binding_id, observation_id, *, text_offset=0, max_chars=20000):
        if type(text_offset) is not int or type(max_chars) is not int or not 1 <= max_chars <= 20000:
            raise HandoffConflict('invalid_read_range')
        value, role, origin, latest = await self.repository.read(binding_id, observation_id)
        if not self.policy.allows(value):
            raise HandoffConflict('content_restricted')
        text = item_text(value)
        if not 0 <= text_offset <= len(text):
            raise HandoffConflict('invalid_read_range')
        end = min(len(text), text_offset + max_chars)
        await self.repository.mark_exposed(binding_id, [value.id])
        return {'protocol_version': 1, 'item': self.item(value, role, origin, latest), 'text': text[text_offset:end],
                'next_text_offset': end if end < len(text) else None, 'truncated': end < len(text)}

    async def _page(self, binding_id, *, cursor=None, limit=20, direction='forward'):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise HandoffConflict('invalid_limit')
        binding = await self.repository.binding(binding_id)
        scope = binding['scope']
        if cursor is None:
            initial = binding['initial_cursor']
            point = datetime.fromisoformat(initial['observed_at']), UUID(initial['id'])
            cursor = self.cursor(scope, direction, *point)
        else:
            point = self.decode_cursor(cursor, scope, direction)
        rows = await self.repository.candidates(binding_id, **{'after' if direction == 'forward' else 'before': point}, limit=500)
        items, identifiers, remaining, next_cursor = [], [], 20000, cursor
        consumed, truncated = 0, False
        for value, role, origin, latest in rows:
            if not self.policy.allows(value):
                consumed += 1
                next_cursor = self.cursor(scope, direction, value.observed_at, value.id)
                continue
            if len(items) >= limit or remaining == 0:
                break
            text = item_text(value)
            excerpt = text[:remaining]
            items.append(self.item(value, role, origin, latest, excerpt=excerpt))
            identifiers.append(value.id)
            remaining -= len(excerpt)
            consumed += 1
            next_cursor = self.cursor(scope, direction, value.observed_at, value.id)
            truncated |= len(excerpt) < len(text)
        if identifiers:
            await self.repository.mark_exposed(binding_id, identifiers)
        return {'protocol_version': 1, 'items': items, 'next_cursor': next_cursor,
                'has_more': consumed < len(rows) or len(rows) == 500, 'truncated': truncated}

    async def history(self, binding_id, *, cursor=None, limit=20):
        return await self._page(binding_id, cursor=cursor, limit=limit, direction='backward')

    async def wait(self, binding_id, *, cursor=None, timeout_seconds=30, limit=20):
        if isinstance(timeout_seconds, bool) or not 0 <= timeout_seconds <= 30:
            raise HandoffConflict('invalid_timeout')
        page = await self._page(binding_id, cursor=cursor, limit=limit)
        if page['items'] or page['has_more'] or timeout_seconds == 0:
            return page
        binding = await self.repository.binding(binding_id)
        scope = binding['scope']
        chat_id = f"{scope['source']}:{scope['account_id']}:{scope['chat_kind']}:{scope['external_id']}"
        with self.notifier.listen(chat_id) as event:
            second = await self._page(binding_id, cursor=page['next_cursor'], limit=limit)
            if second['items'] or second['has_more']:
                return second
            try:
                await asyncio.wait_for(event.wait(), timeout_seconds)
            except TimeoutError:
                return second
            return await self._page(binding_id, cursor=second['next_cursor'], limit=limit)

    async def report(self, binding_id, report):
        return await self.repository.report(binding_id, report, policy=self.policy)

    async def finalize(self, binding_id, evidence):
        return await self.repository.finalize(binding_id, evidence)
