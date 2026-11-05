import json
from icarus.core.plugin import BasePlugin
from argus_app.contracts.events import ObservationRecorded
from argus_app.contracts.source import SourceDescriptor, sent_at


def legacy_source(source_key, content, detail):
    parts = source_key.split(':', 3)
    if len(parts) != 4 or parts[0] != 'feishu' or parts[2] != 'chat':
        return None
    return SourceDescriptor(platform='feishu', kind='unknown', region_id=parts[3],
        tenant_id=parts[1]).model_dump(exclude_none=True)


class FeishuProtocol(BasePlugin):
    def __init__(self, plugin_id, store):
        super().__init__(plugin_id)
        self.store = store

    async def receive(self, payload: dict):
        # Envelope signature/challenge verification belongs at the HTTP route.
        if payload.get('header', {}).get('event_type') != 'im.message.receive_v1':
            raise ValueError('expected official Feishu message event')
        tenant = payload.get('header', {}).get('tenant_key')
        if not isinstance(tenant, str) or not tenant or ':' in tenant or len(tenant) > 128:
            raise ValueError('Feishu tenant_key is required')
        body = payload['event']
        message = body['message']
        raw = message.get('content', '{}')
        content = json.loads(raw) if isinstance(raw, str) else raw
        sender = body.get('sender', {}).get('sender_id', {})
        kind = {'p2p': 'private', 'group': 'group'}.get(message.get('chat_type'), 'unknown')
        normalized = {'text': content.get('text', ''), 'sender_id': sender.get('open_id', ''),
                      'message_type': message.get('message_type', 'text')}
        detail = {'protocol': 'feishu-v2', 'tenant_key': tenant,
            'source': SourceDescriptor(platform='feishu', kind=kind, region_id=message['chat_id'],
                tenant_id=tenant).model_dump(exclude_none=True)}
        for key in ('root_id', 'parent_id', 'thread_id'):
            value = message.get(key)
            if isinstance(value, str) and value and len(value) <= 256:
                detail[key] = value
        if 'parent_id' in detail:
            detail['reply_to'] = detail['parent_id']
        timestamp = sent_at(message.get('create_time'), milliseconds=True)
        if timestamp:
            detail['sent_at'] = timestamp
        media_kind = normalized['message_type']
        if media_kind in {'image', 'audio', 'media', 'file', 'sticker'}:
            normalized['media'] = [{'type': media_kind, 'availability': 'unavailable'}]
            normalized['media_summary'] = '[' + media_kind + ': content unavailable]'
        row = await self.store.ingest(source_key=f"feishu:{tenant}:chat:{message['chat_id']}",
            message_id=message['message_id'], kind='message.observed', content=normalized, detail=detail)
        await self.publish(ObservationRecorded(observation_id=row.id, source_key=row.source_key))
        return row
