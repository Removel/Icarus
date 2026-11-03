import asyncio
from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.events import RulePassed


class BridgePlugin(BasePlugin):
    def __init__(self, plugin_id, store, bridge_client, argus_url, *, rule_files=None, guide=None):
        super().__init__(plugin_id)
        self.store, self.client, self.argus_url = store, bridge_client, argus_url.rstrip('/')
        self.rule_files, self.guide = rule_files, guide or {}
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
                if (result.get('status') in {'failed', 'cancelled', 'completed'}
                        and result.get('commit_state') not in {'pending', 'unknown'}
                        and result.get('replay_safe') is True):
                    await self.store.reconcile_round(channel_id, round_['wake_id'], result['status'])
                elif (result.get('status') in {'failed', 'cancelled', 'completed'}
                      and result.get('commit_state') != 'applied'):
                    await self.store.mark_round_outcome_unknown(channel_id, round_['wake_id'])
                return result
            channel = await self.store.channel(channel_id)
            if 'session_key' not in round_:
                raise RuntimeError('legacy_round_requires_reconciliation')
            session_id = round_.get('session_id') or await self.client.ensure_session(
                channel_id, round_['wake_id'], round_['session_key'])
            items = [(await self.store.observation(id)).model_dump(mode='json') for id in round_['observation_ids']]
            from argus_app.rule_sdk.guide import channel_snapshot
            if channel.version != round_.get('channel_version', channel.version):
                raise RuntimeError('stale_round_snapshot')
            if self.rule_files is None:
                raise RuntimeError('rule_files_unavailable')
            result = await self.client.wake(channel_id=channel_id, wake_id=round_['wake_id'],
                binding_id=round_['binding_id'], channel_version=round_.get('channel_version', channel.version),
                channel_snapshot=channel_snapshot(channel, self.rule_files), rule_sdk=self.guide,
                session_id=session_id, session_key=round_['session_key'], items=items, argus_url=self.argus_url)
            await self.store.set_round_delivery(channel_id, round_['wake_id'], session_id, result['task_id'])
            return result
