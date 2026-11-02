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
async def test_agent_persists_control_before_directed_request():
    store = StoreFake()
    plugin = AgentProtocol('agent-protocol', store)
    class Requester:
        async def request(self, event):
            assert store.rows[0].kind == 'agent.delivered'
            assert store.rows[0].detail == {'binding_id': 'binding', 'payload': {'observation_ids': ['obs']}}
            assert isinstance(event, RuleControlRequest)
            return RuleResponse(correlation_id=event.correlation_id, status=CapabilityStatus.OK, data={'ok': True})
    plugin.bind_requester(Requester(), None)
    assert await plugin.submit('binding', 'request', 'delivered', {'observation_ids': ['obs']}) == {'ok': True}



@pytest.mark.asyncio
async def test_finished_binding_duplicate_returns_stored_receipt():
    class Store:
        async def ingest(self, **values):
            return SimpleNamespace(id='control', detail={'result': {'applied': True}})
        async def bound_channel(self, binding):
            raise ValueError('closed binding must not block receipt replay')
    plugin = AgentProtocol('agent-protocol', Store())
    assert await plugin.submit('closed', 'commit-request', 'commit', {'results': []}) == {'applied': True}
