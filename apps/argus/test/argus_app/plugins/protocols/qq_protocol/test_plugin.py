from types import SimpleNamespace

import pytest

from argus_app.contracts.capabilities import RuleControlRequest, RuleResponse
from argus_app.contracts.events import ObservationRecorded
from argus_app.plugins.protocols.qq_protocol.plugin import QQProtocol
from argus_app.plugins.protocols.feishu_protocol.plugin import FeishuProtocol
from argus_app.plugins.protocols.agent_protocol.plugin import AgentProtocol
from icarus.core.capabilities import CapabilityStatus


class StoreFake:
    def __init__(self):
        self.rows = []
    async def ingest(self, **values):
        row = SimpleNamespace(id=str(len(self.rows)), **values)
        self.rows.append(row)
        return row
    async def bound_channel(self, binding):
        assert binding == 'binding'
        return SimpleNamespace(id='channel')




@pytest.mark.asyncio
async def test_qq_persists_before_publishing():
    store = StoreFake()
    plugin = QQProtocol('qq-protocol', store)
    seen = []
    async def publish(event):
        assert len(store.rows) == 1
        assert isinstance(event, ObservationRecorded)
        seen.append(event)
    plugin.bind_publisher(publish)
    row = await plugin.receive({'post_type': 'message', 'self_id': 100, 'message_type': 'group', 'group_id': 42, 'message_id': 7, 'user_id': 8, 'raw_message': 'hello'})
    assert row.source_key == 'qq:100:group:42'
    assert row.content['text'] == 'hello'
    assert seen[0].observation_id == row.id


@pytest.mark.asyncio
async def test_accounts_same_room_and_message_have_distinct_source_identity():
    plugin = QQProtocol('qq-protocol', StoreFake())
    async def publish(event):
        pass
    plugin.bind_publisher(publish)
    payload = {'post_type': 'message', 'message_type': 'group', 'group_id': 42, 'message_id': 7, 'user_id': 8, 'raw_message': 'hello'}
    with pytest.raises(ValueError, match='self_id'):
        await plugin.receive(payload)
    first = await plugin.receive({**payload, 'self_id': 100})
    second = await plugin.receive({**payload, 'self_id': 200})
    assert first.source_key != second.source_key
    assert 'payload' not in first.detail


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['notice', 'meta_event'])
async def test_onebot_non_incoming_events_are_rejected(kind):
    store = StoreFake()
    payload = {'post_type': kind, 'self_id': 100, 'message_type': 'group', 'group_id': 42,
               'user_id': 8, 'message_id': 'string-id' if kind == 'message_sent' else 7,
               'raw_message': 'bot output' if kind == 'message_sent' else ''}
    if kind == 'notice':
        payload.update(notice_type='notify', sub_type='poke')
    with pytest.raises(ValueError, match='message'):
        await QQProtocol('qq-protocol', store).receive(payload)
    assert store.rows == []


async def test_self_private_uses_target_peer_and_preserves_segments():
    store = StoreFake()
    plugin = QQProtocol('qq-protocol', store)
    async def publish(event):
        pass
    plugin.bind_publisher(publish)
    row = await plugin.receive({'post_type': 'message_sent', 'self_id': 100,
        'message_type': 'private', 'user_id': 100, 'target_id': 200, 'message_id': -9,
        'message': [{'type': 'reply', 'data': {'id': '8'}},
                    {'type': 'at', 'data': {'qq': '200'}},
                    {'type': 'text', 'data': {'text': '请回应'}}]})
    assert row.source_key == 'qq:100:private:200'
    assert row.message_id == '-9'
    assert row.content['text'] == '请回应'
    assert row.content['message_type'] == 'private'
    assert row.content['segments'][1] == {'type': 'at', 'data': {'qq': '200'}}
    assert row.detail['is_self'] is True
    assert row.detail['peer_id'] == '200'
    assert row.detail['reply_to'] == '8'


async def test_self_private_without_peer_is_rejected():
    from argus_app.contracts.models import DomainError
    with pytest.raises(DomainError) as error:
        await QQProtocol('qq', StoreFake()).receive({'post_type': 'message_sent',
            'self_id': 100, 'message_type': 'private', 'user_id': 100,
            'message_id': 'm', 'raw_message': 'hello'})
    assert error.value.code == 'qq_peer_missing'


async def test_group_field_on_temp_private_does_not_change_source():
    plugin = QQProtocol('qq', StoreFake())
    async def publish(event):
        pass
    plugin.bind_publisher(publish)
    row = await plugin.receive({'post_type': 'message', 'self_id': 100,
        'message_type': 'private', 'sub_type': 'group', 'group_id': 42,
        'user_id': 200, 'message_id': 'm', 'raw_message': 'temp'})
    assert row.source_key == 'qq:100:private:200'
    assert row.detail['temp_group_id'] == '42'


@pytest.mark.parametrize('kind', ['meta_event', 'notice', 'request', 'unknown', {}, []])
async def test_http_nonmessage_policy_after_signature_validation(tmp_path, kind):
    import hashlib, hmac, json
    import httpx
    from pydantic import SecretStr
    from argus_app.application import create_app
    from argus_app.config import ArgusConfig
    store = StoreFake()
    plugin = QQProtocol('qq', store)
    app = create_app(ArgusConfig(database_url=SecretStr('postgresql+asyncpg://unused/test'),
        data_dir=tmp_path, agent_token=SecretStr('a'), gateway_token=SecretStr('g'),
        qq_token=SecretStr('synthetic-secret')))
    app.state.ready = True
    app.state.host = SimpleNamespace(get_plugin=lambda name: plugin)
    raw = json.dumps({'post_type': kind, 'self_id': 100}).encode()
    signature = 'sha1=' + hmac.new(b'synthetic-secret', raw, hashlib.sha1).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        bad = await client.post('/protocols/qq', content=raw, headers={'X-Signature': 'wrong'})
        good = await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})
    assert bad.status_code == 401
    normal_noop = kind in ('meta_event', 'notice', 'request')
    assert good.status_code == (200 if normal_noop else 422)
    if normal_noop:
        assert good.json() == {'accepted': True, 'ignored': True, 'reason': 'unsupported_event_type'}
    assert store.rows == []
