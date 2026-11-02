import json
from icarus.core.plugin import BasePlugin
from argus_app.contracts.events import ObservationRecorded


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
        row = await self.store.ingest(source_key=f"feishu:{tenant}:chat:{message['chat_id']}", message_id=message['message_id'], kind='message.observed', content={'text': content.get('text', ''), 'sender_id': sender.get('open_id', ''), 'message_type': message.get('message_type', 'text')}, detail={'protocol': 'feishu-v2', 'tenant_key': tenant})
        await self.publish(ObservationRecorded(observation_id=row.id, source_key=row.source_key))
        return row
