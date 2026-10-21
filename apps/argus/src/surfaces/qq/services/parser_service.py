"""Validate OneBot reports without platform actions or database access."""

from datetime import UTC, datetime
from math import isfinite
import unicodedata

from ....common.schemas.observation import ContentPart, ObservedItemDraft
from ..schemas.onebot import OneBotEvent


def positive_id(value, name):
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return str(int(value))
    raise ValueError(f'{name} must be a positive identifier')


def message_id(value):
    if type(value) is int and value != 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.lstrip('-').isdecimal() and int(value) != 0:
        return str(int(value))
    raise ValueError('message_id must be a nonzero signed integer')


def normalized(value):
    return unicodedata.normalize('NFKC', value).casefold()


class QQParser:
    def __init__(self, bot_id: str, bot_names=()):
        self.bot_id = positive_id(bot_id, 'bot_id')
        self.bot_names = tuple(normalized(name) for name in bot_names if name.strip())

    def parse(self, raw):
        if not isinstance(raw, dict):
            raise ValueError('OneBot event must be an object')
        event = OneBotEvent.model_validate(raw)
        if positive_id(raw['self_id'], 'self_id') != self.bot_id:
            raise ValueError('self_id does not match configured account')
        recall = event.post_type == 'notice' and raw.get('notice_type') in ('group_recall', 'friend_recall')
        if event.post_type in ('meta_event', 'request') or (event.post_type == 'notice' and not recall):
            return None
        if event.post_type not in ('message', 'message_sent') and not recall:
            raise ValueError('Unsupported OneBot event')
        identifier = message_id(raw.get('message_id'))
        kind = ('group' if raw['notice_type'] == 'group_recall' else 'private') if recall else raw.get('message_type')
        if kind not in ('private', 'group'):
            raise ValueError('message_type must be group or private')
        sender = raw.get('sender', {})
        if not isinstance(sender, dict):
            raise ValueError('sender must be an object')
        sender_id = positive_id(raw.get('user_id', sender.get('user_id')), 'user_id')
        is_self = event.post_type == 'message_sent' or sender_id == self.bot_id
        target = positive_id(raw.get('group_id'), 'group_id') if kind == 'group' else (
            positive_id(raw.get('target_id'), 'target_id') if is_self and not recall else sender_id)
        parts, mentions = [], False
        if not recall:
            body = raw.get('message')
            if isinstance(body, str):
                if '[CQ:' in body:
                    raise ValueError('CQ strings require messageFormat=array')
                parts.append(ContentPart(kind='text', value=body))
            elif isinstance(body, list):
                for segment in body:
                    if not isinstance(segment, dict) or not isinstance(segment.get('data'), dict):
                        raise ValueError('Message segment data must be an object')
                    data, segment_type = segment['data'], segment.get('type')
                    if segment_type == 'text':
                        if not isinstance(data.get('text'), str):
                            raise ValueError('Text segment must contain text')
                        parts.append(ContentPart(kind='text', value=data['text']))
                    elif segment_type == 'image':
                        if not isinstance(data.get('file'), str) or not data['file']:
                            raise ValueError('Image segment requires reference')
                        parts.append(ContentPart(kind='image', value=data['file']))
                    elif segment_type == 'at':
                        target_account = str(data.get('qq'))
                        if target_account != 'all':
                            target_account = positive_id(data.get('qq'), 'at.qq')
                        parts.append(ContentPart(kind='text', value=f'@{target_account}'))
                        mentions |= target_account == self.bot_id
                    elif isinstance(segment_type, str) and segment_type:
                        parts.append(ContentPart(kind='other', value=segment_type))
                    else:
                        raise ValueError('Segment requires type')
            else:
                raise ValueError('message must be text or array')
        stamp = raw.get('time')
        try:
            if type(stamp) not in (int, float) or not isfinite(stamp):
                raise ValueError()
            sent_at = datetime.fromtimestamp(stamp, UTC)
        except (ValueError, OverflowError, OSError):
            raise ValueError('Invalid Unix timestamp') from None
        text = normalized('\n'.join(part.value for part in parts if part.kind == 'text'))
        name_hit = any(name in text for name in self.bot_names)
        return ObservedItemDraft(source='qq', chat_id=f'qq:{self.bot_id}:{kind}:{target}',
            message_id=identifier, deleted=recall, is_self=is_self,
            strong_signal=not recall and not is_self and (kind == 'private' or mentions or name_hit),
            sender_id=sender_id, sender_name=sender.get('card') or sender.get('nickname'),
            sent_at=sent_at, observed_at=datetime.now(UTC), content=tuple(parts),
            locator={'account_id': self.bot_id, 'chat_kind': kind, 'external_id': target,
                     'message_id': identifier, 'time': stamp},
            detail={'mentions_bot': mentions, 'bot_name_hit': name_hit})
