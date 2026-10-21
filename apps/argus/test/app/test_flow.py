"""Group backlog is reconsidered only after the preceding request terminates."""
import asyncio
import hashlib
import hmac
import json

from httpx import ASGITransport, AsyncClient

from apps.argus.src.core.config import ArgusConfig, QQConfig
from apps.argus.src.common.schemas.wake import WakeOutcome, WakeReceipt


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not await predicate():
            await asyncio.sleep(.01)


async def test_group_backlog_and_other_group_progress_independently(database):
    from apps.argus.src.main import create_app
    released = asyncio.Event()
    class ControlledAgent:
        def __init__(self):
            self.payloads = {}
            self.first_id = None
        async def submit(self, payload):
            self.payloads[payload.wake_id] = payload
            if self.first_id is None:
                self.first_id = payload.wake_id
            return WakeReceipt(accepted=True)
        async def get_outcome(self, wake_id):
            if wake_id == self.first_id and not released.is_set():
                return None
            payload = self.payloads[wake_id]
            return WakeOutcome(wake_id=wake_id, item_ids=tuple(item.item_id for item in payload.items),
                               status='resolved', simulated=True, reason='controlled completion')
    agent = ControlledAgent()
    app = create_app(ArgusConfig(scan_interval_seconds=.01,
        qq=QQConfig(enabled=True, bot_id='123', bot_names=('Icarus',))), database=database,
        agent=agent, qq_secret='secret')
    async with app.router.lifespan_context(app):
        repo = app.state.repository
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            async def post(message_id, group_id, text):
                raw = json.dumps({'self_id': 123, 'post_type': 'message', 'message_type': 'group',
                    'message_id': message_id, 'group_id': group_id, 'user_id': 456,
                    'message': text, 'time': 1700000000}).encode()
                response = await client.post('/api/argus/qq/onebot/events', content=raw,
                    headers={'X-Self-ID': '123', 'X-Signature': 'sha1=' + hmac.new(b'secret', raw, hashlib.sha1).hexdigest()})
                assert response.status_code == 204
            await post(1, 999, '@Icarus 总结文章')
            async def accepted():
                wake = await repo.active_wake('qq:123:group:999')
                return wake is not None and wake.status == 'accepted'
            await wait_for(accepted)
            await post(2, 999, '吃火锅')
            await post(3, 999, '@Icarus 看看报错')
            await post(4, 888, '@Icarus 另一个群的问题')
            async def other_resolved():
                return any(wake.status == 'resolved' for wake in await repo.list_wakes(chat_id='qq:123:group:888'))
            await wait_for(other_resolved)
            assert len(await repo.list_wakes(chat_id='qq:123:group:999')) == 1
            assert len(await repo.latest_open_items('qq:123:group:999', limit=20)) == 2
            released.set()
            async def both_resolved():
                values = await repo.list_wakes(chat_id='qq:123:group:999')
                return len(values) == 2 and all(wake.status == 'resolved' for wake in values)
            await wait_for(both_resolved)
            assert await repo.latest_open_items('qq:123:group:999', limit=20) == ()
