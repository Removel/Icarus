from icarus.core.plugin import BasePlugin
from argus_app.contracts.events import ObservationRecorded


class QQProtocol(BasePlugin):
    def __init__(self, plugin_id, store):
        super().__init__(plugin_id)
        self.store = store

    async def receive(self, payload: dict):
        if payload.get('post_type', 'message') != 'message':
            raise ValueError('expected OneBot message event')
        account = payload.get('self_id')
        if not isinstance(account, (str, int)) or isinstance(account, bool) or not str(account).isdigit():
            raise ValueError('QQ self_id is required')
        group = payload.get('message_type') == 'group' or 'group_id' in payload
        source = f"qq:{account}:group:{payload['group_id']}" if group else f"qq:{account}:private:{payload['user_id']}"
        text = payload.get('raw_message')
        if text is None:
            message = payload.get('message', '')
            text = message if isinstance(message, str) else ''.join(s.get('data', {}).get('text', '') for s in message if s.get('type') == 'text')
        row = await self.store.ingest(source_key=source, message_id=str(payload['message_id']), kind='message.observed', content={'text': text, 'sender_id': str(payload.get('user_id', ''))}, detail={'protocol': 'onebot', 'self_id': str(account)})
        await self.publish(ObservationRecorded(observation_id=row.id, source_key=row.source_key))
        return row
