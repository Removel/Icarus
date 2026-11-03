import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from argus_app.infrastructure.store import Store
from argus_app.application import recovery_scan_once


@pytest_asyncio.fixture
async def isolated_store():
    url = os.environ.get('ARGUS_TEST_DATABASE_URL')
    if not url:
        pytest.skip('isolated PostgreSQL required')
    schema = 'argus_r_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    store = Store(url)
    await store.engine.dispose()
    store.engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    try:
        await store.start()
        yield store
    finally:
        await store.close()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_round_budget_matches_gateway_without_silent_truncation(isolated_store):
    store = isolated_store
    channel = await store.create_channel('test', 'goal', ['qq:1'])
    for index in range(20):
        item = await store.ingest('qq:1', str(index), 'message.observed', {'text': '消息' * 5000})
        assert await store.decide(channel.id, item.id, channel.version, {'passed': True, 'reason': 'match'})
    round_ = await store.begin_round(channel.id)
    items = [(await store.observation(id)).model_dump(mode='json') for id in round_['observation_ids']]
    assert 0 < len(items) < 20
    assert len(json.dumps(items).encode()) <= 384 * 1024
    assert all(item['content']['text'] == '消息' * 5000 for item in items)
    # The real Gateway/Agent shared validator must accept this exact aggregate.
    from agent_app.argus_protocol import WakeRequest
    WakeRequest(channel_id=channel.id, wake_id=round_['wake_id'], binding_id=round_['binding_id'],
                channel_version=channel.version, session_key=round_['session_key'], session_id=str(uuid4()), items=items, argus_url='http://argus.test',
                channel_snapshot=dict(id=channel.id,title=channel.title,goal=channel.goal,progress=channel.progress,
                                      lifecycle=channel.lifecycle,version=channel.version,source_keys=list(channel.subscriptions),rule_source=''),
                rule_sdk={'version':2})


@pytest.mark.asyncio
async def test_single_oversize_exhausted_but_later_small_item_deliverable(isolated_store):
    store = isolated_store
    channel = await store.create_channel('test', 'goal', ['qq:1'])
    big = await store.ingest('qq:1', 'big', 'message.observed', {'text': 'x' * (400 * 1024)})
    small = await store.ingest('qq:1', 'small', 'message.observed', {'text': 'small'})
    for item in [big, small]:
        await store.decide(channel.id, item.id, channel.version, {'passed': True, 'reason': 'match'})
    round_ = await store.begin_round(channel.id)
    assert round_['observation_ids'] == [small.id]
    pending = (await store.channel(channel.id)).runtime['pending'][big.id]
    assert pending['delivery_attempt_count'] == 3
    assert pending['delivery_error'] == 'delivery_item_too_large'


@pytest.mark.asyncio
@pytest.mark.parametrize('lifecycle', ['paused', 'completed'])
async def test_recovery_scan_reconciles_inactive_open_round_and_isolates_failures(lifecycle):
    class Rules:
        async def scan_once(self):
            raise RuntimeError('one channel backpressure')
    class Bridge:
        ids = []
        async def deliver_channel(self, id):
            self.ids.append(id)
            if id == 'broken':
                raise RuntimeError('remote unavailable')
    bridge = Bridge()
    channels = [SimpleNamespace(id='broken', lifecycle='active', runtime={}),
                SimpleNamespace(id='recover', lifecycle=lifecycle, runtime={'round': {'wake_id': 'wake'}}),
                SimpleNamespace(id='inactive', lifecycle=lifecycle, runtime={})]
    class Store:
        async def channels(self):
            return channels
    class Host:
        def get_plugin(self, name):
            return Rules() if name == 'workflow-rules' else bridge
    await recovery_scan_once(Host(), Store())
    assert bridge.ids == ['broken', 'recover']


@pytest.mark.asyncio
@pytest.mark.parametrize('lifecycle', ['paused', 'completed'])
@pytest.mark.parametrize('replay_safe', [True, False])
async def test_real_inactive_round_reconciles_only_when_safe(isolated_store, lifecycle, replay_safe):
    from argus_app.plugins.icarus_bridge.plugin import BridgePlugin
    store = isolated_store
    channel = await store.create_channel('test', 'goal', ['qq:1'])
    item = await store.ingest('qq:1', 'item', 'message.observed', {'text': 'hello'})
    await store.decide(channel.id, item.id, channel.version, {'passed': True, 'reason': 'match'})
    round_ = await store.begin_round(channel.id)
    await store.set_round_delivery(channel.id, round_['wake_id'], str(uuid4()), str(uuid4()))
    # Model the committed lifecycle update followed by a process crash.
    async with store.engine.begin() as conn:
        await conn.execute(text('UPDATE channels SET lifecycle=:state WHERE id=:id'), {'state': lifecycle, 'id': channel.id})
    class Client:
        async def status(self, wake):
            assert wake == round_['wake_id']
            return {'status': 'failed', 'replay_safe': replay_safe}
    await BridgePlugin('icarus-bridge', store, Client(), 'http://argus.test').deliver_channel(channel.id)
    restored = await store.channel(channel.id)
    if not replay_safe:
        assert restored.runtime['round']['wake_id'] == round_['wake_id']
        assert restored.runtime['round']['outcome'] == 'execution_outcome_unknown'
        assert restored.runtime['pending'][item.id]['delivery_attempt_count'] == 0
        return
    assert restored.runtime['round'] is None
    assert await store.begin_round(channel.id) is None
    if lifecycle == 'paused':
        assert item.id in restored.runtime['pending']
        assert restored.runtime['pending'][item.id]['delivery_attempt_count'] == 1
