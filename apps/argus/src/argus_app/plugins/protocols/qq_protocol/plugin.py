"""Normalize OneBot message identity; business response policy belongs to rules."""
import re

from icarus.core.plugin import BasePlugin
from argus_app.contracts.events import ObservationRecorded
from argus_app.contracts.models import DomainError
from argus_app.contracts.source import SourceDescriptor, sent_at


def _identity(value, code):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit() or int(value) <= 0:
        raise DomainError(code)
    return str(value)


def _cq_segments(text):
    """Normalize OneBot's alternate string representation before field filtering."""
    segments, offset = [], 0
    for match in re.finditer(r'\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]', text):
        if match.start() > offset:
            segments.append({'type':'text','data':{'text':text[offset:match.start()]}})
        data = {}
        for field in match.group(2).lstrip(',').split(','):
            key, separator, value = field.partition('=')
            if separator:
                data[key] = value
        segments.append({'type':match.group(1),'data':data})
        offset = match.end()
        if len(segments) > 256:
            raise DomainError('qq_segments_too_large')
    if offset < len(text):
        segments.append({'type':'text','data':{'text':text[offset:]}})
    return segments


def normalize_message(payload: dict) -> tuple[str, str, dict, dict]:
    post_type = payload.get('post_type', 'message')
    if post_type not in {'message', 'message_sent'}:
        raise ValueError('expected OneBot message event')
    account = payload.get('self_id')
    if isinstance(account, bool) or not isinstance(account, (str, int)) or not str(account).isdigit() or int(account) <= 0:
        raise ValueError('QQ self_id is required')
    account = str(account)
    sender = _identity(payload.get('user_id'), 'qq_sender_invalid')
    is_self = post_type == 'message_sent' or sender == account
    kind = payload.get('message_type')
    if kind not in {'group', 'private'}:
        raise DomainError('qq_message_type_invalid')
    peer = (_identity(payload.get('group_id'), 'qq_peer_missing') if kind == 'group'
            else _identity(payload.get('target_id') if is_self else sender, 'qq_peer_missing'))
    if kind == 'private' and peer == account:
        raise DomainError('qq_peer_missing')
    message_id = payload.get('message_id')
    if isinstance(message_id, bool) or not isinstance(message_id, (str, int)) or not str(message_id) or len(str(message_id)) > 128:
        raise DomainError('qq_message_id_invalid')
    message = payload.get('message')
    if message is None or message == [] or message == '':
        message = payload.get('raw_message', '')
    if isinstance(message, str):
        message = _cq_segments(message)
    segments = []
    if isinstance(message, list):
        if len(message) > 256:
            raise DomainError('qq_segments_too_large')
        for segment in message:
            if not isinstance(segment, dict) or not isinstance(segment.get('type'), str) or not isinstance(segment.get('data', {}), dict):
                raise DomainError('qq_segments_invalid')
            tag, data = segment['type'], segment.get('data', {})
            # Keep useful text/reply/@ metadata, never inline media blobs or URLs.
            keys = {'text': ('text',), 'at': ('qq',), 'reply': ('id',)}.get(tag, ())
            normalized = {}
            for key in keys:
                value = data.get(key)
                if not isinstance(value, (str, int)) or isinstance(value, bool):
                    raise DomainError('qq_segments_invalid')
                normalized[key] = str(value)
            segments.append({'type': tag, 'data': normalized})
    elif not isinstance(message, str):
        raise DomainError('qq_segments_invalid')
    media = []
    if isinstance(message, list):
        for segment in message:
            tag, data = segment['type'], segment.get('data', {})
            if tag in {'image', 'record', 'video', 'file', 'face', 'mface'}:
                item = {'type': tag, 'availability': 'unavailable'}
                # Never forward URLs, encoded blobs or arbitrary nested platform data.
                key = 'id' if tag in {'face', 'mface'} else 'file'
                value = data.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    value = str(value)
                    if len(value) <= 256 and not any(c in value for c in (':', '/', '\\')):
                        item['id'] = value
                media.append(item)
        text = ''.join(s['data']['text'] for s in segments if s['type'] == 'text')
    else:
        text = payload.get('raw_message', message)
    if not isinstance(text, str):
        raise DomainError('qq_text_invalid')
    content = {'text': text, 'sender_id': sender, 'message_type': kind, 'segments': segments}
    sender_info = payload.get('sender')
    if isinstance(sender_info, dict):
        name = sender_info.get('card') or sender_info.get('nickname')
        if isinstance(name, str) and name:
            content['sender_name'] = name[:256]
    if media:
        content['media'] = media
        content['media_summary'] = ' '.join('[' + m['type'] + ': content unavailable]' for m in media)
    detail = {'protocol': 'onebot', 'self_id': account, 'is_self': is_self,
              'peer_id': peer, 'schema_version': 2,
              'source': SourceDescriptor(platform='qq', kind=kind, region_id=peer, account_id=account).model_dump(exclude_none=True)}
    timestamp = sent_at(payload.get('time'))
    if timestamp:
        detail['sent_at'] = timestamp
    reply = next((s['data']['id'] for s in segments if s['type'] == 'reply'), None)
    if reply is not None:
        detail['reply_to'] = reply
    if kind == 'private' and payload.get('sub_type') == 'group':
        group = payload.get('group_id') or payload.get('sender', {}).get('group_id')
        if group is not None:
            detail['temp_group_id'] = _identity(group, 'qq_temp_group_invalid')
    return f'qq:{account}:{kind}:{peer}', str(message_id), content, detail


def legacy_source(source_key, content, detail):
    parts = source_key.split(':', 3)
    if len(parts) != 4 or parts[0] != 'qq' or parts[2] not in {'group', 'private'}:
        return None
    return SourceDescriptor(platform='qq', kind=parts[2], region_id=parts[3],
        account_id=str(detail.get('self_id', parts[1]))).model_dump(exclude_none=True)


class QQProtocol(BasePlugin):
    def __init__(self, plugin_id, store):
        super().__init__(plugin_id)
        self.store = store

    async def receive(self, payload: dict):
        source, message_id, content, detail = normalize_message(payload)
        row = await self.store.ingest(source_key=source, message_id=message_id,
            kind='message.observed', content=content, detail=detail)
        await self.publish(ObservationRecorded(observation_id=row.id, source_key=row.source_key))
        return row
