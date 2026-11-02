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
async def test_feishu_official_event_normalizes_text():
    plugin = FeishuProtocol('feishu-protocol', StoreFake())
    async def publish(event):
        pass
    plugin.bind_publisher(publish)
    row = await plugin.receive({'header': {'tenant_key': 'tenant', 'event_type': 'im.message.receive_v1'}, 'event': {'message': {'message_id': 'm', 'chat_id': 'c', 'message_type': 'text', 'content': '{"text":"hi"}'}, 'sender': {'sender_id': {'open_id': 'u'}}}})
    assert row.source_key == 'feishu:tenant:chat:c'
    assert row.content['text'] == 'hi'


@pytest.mark.asyncio
async def test_tenants_same_chat_and_message_have_distinct_source_identity():
    plugin = FeishuProtocol('feishu-protocol', StoreFake())
    async def publish(event):
        pass
    plugin.bind_publisher(publish)
    payload = {'header': {'event_type': 'im.message.receive_v1'}, 'event': {'message': {'message_id': 'm', 'chat_id': 'c', 'content': '{"text":"hi"}'}}}
    with pytest.raises(ValueError, match='tenant_key'):
        await plugin.receive(payload)
    first = await plugin.receive({**payload, 'header': {**payload['header'], 'tenant_key': 'a'}})
    second = await plugin.receive({**payload, 'header': {**payload['header'], 'tenant_key': 'b'}})
    assert first.source_key != second.source_key
    assert 'payload' not in first.detail
