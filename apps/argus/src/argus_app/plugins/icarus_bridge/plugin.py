import asyncio
from datetime import datetime, timezone
import httpx
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
            submission = round_.get('submission', {})
            admission = submission.get('admission_state')
            if admission == 'not_admitted':
                if not submission.get('retryable') or submission.get('attempt', 0) >= 3:
                    return submission
                next_retry = submission.get('next_retry_at')
                if next_retry and datetime.fromisoformat(next_retry) > datetime.now(timezone.utc):
                    return submission
            if round_.get('task_id') or admission in {'accepted', 'submitting', 'unknown'}:
                # Remote completion is not delivered/confirmation; only callback receipts
                # can retire pending observations. Never create another task on status retry.
                result = await self._status(round_['wake_id'])
                return await self._record_result(channel_id, round_, result)
            if round_.get('wake_payload'):
                # The first response may have been lost. Inspect the original claim
                # before resending exactly the persisted immutable request.
                try:
                    result = await self._status(round_['wake_id'])
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 404:
                        raise
                    result = None
                if result is not None and result.get('admission_state') not in {'prepared','not_admitted'}:
                    return await self._record_result(channel_id, round_, result)
                if result is not None and not result.get('retryable'):
                    return await self._record_result(channel_id, round_, result)
                result = await self.client.wake(**round_['wake_payload'])
                return await self._record_result(channel_id, round_, result)
            channel = await self.store.channel(channel_id)
            if 'session_key' not in round_:
                raise RuntimeError('legacy_round_requires_reconciliation')
            items = [(await self.store.observation(id)).model_dump(mode='json') for id in round_['observation_ids']]
            from argus_app.rule_sdk.guide import channel_snapshot
            if channel.version != round_.get('channel_version', channel.version):
                raise RuntimeError('stale_round_snapshot')
            if self.rule_files is None:
                raise RuntimeError('rule_files_unavailable')
            snapshot = channel_snapshot(channel, self.rule_files)
            session_id = round_.get('session_id') or await self.client.ensure_session(
                channel_id, round_['wake_id'], round_['session_key'])
            await self.store.set_round_session(channel_id, round_['wake_id'], session_id)
            payload = dict(channel_id=channel_id, wake_id=round_['wake_id'],
                binding_id=round_['binding_id'], channel_version=round_.get('channel_version', channel.version),
                channel_snapshot=snapshot, rule_sdk=self.guide,
                session_id=session_id, session_key=round_['session_key'], items=items, argus_url=self.argus_url)
            await self.store.set_round_request(channel_id, round_['wake_id'], payload)
            result = await self.client.wake(**payload)
            return await self._record_result(channel_id, round_ | {'session_id':session_id}, result)

    async def _status(self, wake_id):
        return await self.client.status(wake_id)

    async def _record_result(self, channel_id, round_, result):
        admission = result.get('admission_state')
        if admission:
            await self.store.set_round_submission(channel_id, round_['wake_id'], admission,
                attempt=result['attempt'], retryable=result.get('retryable', False), error_code=result.get('error_code'))
        if (admission == 'accepted' or admission is None) and not round_.get('task_id'):
            await self.store.set_round_delivery(channel_id, round_['wake_id'], round_.get('session_id') or result['session_id'], result['task_id'])
        if result.get('status') in {'failed','cancelled','completed'}:
            if result.get('replay_safe') is True and result.get('commit_state') not in {'pending','unknown'}:
                await self.store.reconcile_round(channel_id, round_['wake_id'], result['status'])
            elif round_.get('task_id') and result.get('commit_state') != 'applied':
                await self.store.mark_round_outcome_unknown(channel_id, round_['wake_id'])
        return result
