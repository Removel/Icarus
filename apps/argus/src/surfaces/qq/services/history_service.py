"""SnowLuma history adapter for the QQ observation window."""

from __future__ import annotations

import asyncio
import logging

from packages.snowluma import SnowLumaError

from ....common.schemas.observation import ObservedItemDraft
from .parser_service import QQParser

logger = logging.getLogger('argus.qq.history')


def parse_chat_id(chat_id):
    parts = chat_id.split(':', 3)
    if (len(parts) != 4 or parts[0] != 'qq' or parts[2] not in ('group', 'private')
            or not parts[3]):
        raise ValueError('invalid QQ chat id')
    return parts[2], parts[3]


class QQHistoryService:
    """Fetch a bounded recent window from SnowLuma without blocking judgement."""

    def __init__(self, client, parser: QQParser, *, limit=15, fetch_extra=5, timeout=2.0, count_limit=50):
        self.client, self.parser = client, parser
        self.limit, self.fetch_extra = limit, fetch_extra
        self.timeout, self.count_limit = timeout, count_limit

    async def recent(self, chat_id, *, before=None, limit=None) -> tuple[ObservedItemDraft, ...]:
        limit = self.limit if limit is None else max(0, int(limit))
        if limit == 0:
            return ()
        try:
            kind, external_id = parse_chat_id(chat_id)
        except ValueError:
            return ()
        count = min(limit + self.fetch_extra, self.count_limit)
        try:
            call = (self.client.group_history(external_id, count=count)
                    if kind == 'group' else self.client.friend_history(external_id, count=count))
            messages = await asyncio.wait_for(call, self.timeout)
        except (SnowLumaError, TimeoutError, OSError, ValueError) as error:
            logger.warning('SnowLuma history unavailable (%s)', type(error).__name__)
            return ()
        drafts = []
        for message in messages:
            try:
                draft = self.parser.parse(self._event(kind, external_id, message))
            except (ValueError, TypeError, KeyError):
                continue
            if draft is not None:
                drafts.append(draft.model_copy(update={'observed_at': draft.sent_at}))
        if before is not None:
            drafts = [draft for draft in drafts if draft.sent_at < before]
        drafts.sort(key=lambda draft: (draft.sent_at, draft.message_id))
        return tuple(drafts[-limit:])

    def _event(self, kind, external_id, message):
        sender_id = int(message.sender_id)
        raw = {
            'post_type': 'message_sent' if message.is_self else 'message',
            'self_id': int(self.parser.bot_id),
            'message_type': kind,
            'message_id': int(message.message_id),
            'time': message.sent_at.timestamp(),
            'sender': {
                'user_id': sender_id,
                'nickname': message.sender_name or '',
                **({'card': message.sender_name} if message.sender_name else {}),
                **({'role': message.sender_role} if message.sender_role else {}),
            },
            'message': list(message.message) if message.message else (message.raw_message or ''),
        }
        if kind == 'group':
            raw['group_id'] = int(message.group_id or external_id)
        else:
            raw['user_id'] = sender_id
            if message.is_self:
                raw['target_id'] = int(external_id)
        return raw
