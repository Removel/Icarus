import asyncio
from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.events import RulePassed


class BridgePlugin(BasePlugin):
    def __init__(self, plugin_id, store, bridge_client, argus_url):
        super().__init__(plugin_id)
        self.store, self.client, self.argus_url = store, bridge_client, argus_url.rstrip('/')
        self._delivery_lock = asyncio.Lock()

    @handles(RulePassed)
    async def rule_passed(self, source, event):
        await self.deliver_channel(event.channel_id)

    async def deliver_channel(self, channel_id):
        async with self._delivery_lock:
            round_ = await self.store.begin_round(channel_id)
            if not round_ or round_.get('closed'):
                return
            if self.client is None:
                raise RuntimeError('bridge_unavailable')
            if round_.get('task_id'):
                # Remote completion is not delivered/confirmation; only callback receipts
                # can retire pending observations. Never create another task on status retry.
                result = await self.client.status(round_['wake_id'])
                if result.get('status') in {'failed', 'cancelled', 'completed'}:
                    await self.store.reconcile_round(channel_id, round_['wake_id'], result['status'])
                return result
            channel = await self.store.channel(channel_id)
            session_id = round_.get('session_id') or await self.client.ensure_session(channel_id, round_['wake_id'])
            items = [(await self.store.observation(id)).model_dump(mode='json') for id in round_['observation_ids']]
            result = await self.client.wake(channel_id=channel_id, wake_id=round_['wake_id'],
                binding_id=round_['binding_id'], channel_version=round_.get('channel_version', channel.version),
                session_id=session_id, items=items, argus_url=self.argus_url)
            await self.store.set_round_delivery(channel_id, round_['wake_id'], session_id, result['task_id'])
            return result
