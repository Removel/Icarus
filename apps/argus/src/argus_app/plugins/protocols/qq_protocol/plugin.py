"""Normalize OneBot message identity; business response policy belongs to rules."""
from icarus.core.plugin import BasePlugin
from argus_app.contracts.events import ObservationRecorded
from argus_app.contracts.models import DomainError


def _identity(value, code):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit() or int(value) <= 0:
        raise DomainError(code)
    return str(value)


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
    message = payload.get('message', [])
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
    text = payload.get('raw_message')
    if text is None:
        text = message if isinstance(message, str) else ''.join(s['data']['text'] for s in segments if s['type'] == 'text')
    if not isinstance(text, str):
        raise DomainError('qq_text_invalid')
    content = {'text': text, 'sender_id': sender, 'message_type': kind, 'segments': segments}
    detail = {'protocol': 'onebot', 'self_id': account, 'is_self': is_self,
              'peer_id': peer, 'schema_version': 1}
    reply = next((s['data']['id'] for s in segments if s['type'] == 'reply'), None)
    if reply is not None:
        detail['reply_to'] = reply
    if kind == 'private' and payload.get('sub_type') == 'group':
        group = payload.get('group_id') or payload.get('sender', {}).get('group_id')
        if group is not None:
            detail['temp_group_id'] = _identity(group, 'qq_temp_group_invalid')
    return f'qq:{account}:{kind}:{peer}', str(message_id), content, detail


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
