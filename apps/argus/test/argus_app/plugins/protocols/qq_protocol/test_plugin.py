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
