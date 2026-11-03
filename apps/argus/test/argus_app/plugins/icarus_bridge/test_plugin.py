import httpx
import pytest
from types import SimpleNamespace
from argus_app.plugins.icarus_bridge.client import BridgeClient
from argus_app.plugins.icarus_bridge.plugin import BridgePlugin


@pytest.mark.asyncio
async def test_bridge_client_exact_frozen_wire():
    seen = []
    async def transport(request):
        import json
        seen.append((request.url.path, json.loads(request.content)))
        response = {'/argus/session': {'session_id': 'session'}, '/argus/wake': {'task_id': 'task', 'status': 'accepted'}, '/argus/status': {'session_id': 'session', 'task_id': 'task', 'status': 'completed'}}
        return httpx.Response(200, json=response[request.url.path])
    async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as http:
        client = BridgeClient(http)
        assert await client.ensure_session('ch', 'request', 'slot-a') == 'session'
        assert await client.wake(channel_id='ch', wake_id='wake') == {'task_id': 'task', 'status': 'accepted'}
        assert (await client.status('wake'))['status'] == 'completed'
    assert seen == [('/argus/session', {'channel_id': 'ch', 'request_id': 'request', 'session_key': 'slot-a'}), ('/argus/wake', {'channel_id': 'ch', 'wake_id': 'wake'}), ('/argus/status', {'wake_id': 'wake'})]


@pytest.mark.asyncio
async def test_delivery_retry_keeps_identity_and_does_not_claim_actual_delivery():
    class Store:
        def __init__(self):
            self.round = {'wake_id': 'wake', 'session_key': 'slot-a', 'binding_id': 'binding', 'observation_ids': ['obs'], 'delivered': [], 'confirmed': {}, 'closed': False}
            self.delivery = []
        async def begin_round(self, channel_id):
            return self.round
        async def channel(self, id):
            return SimpleNamespace(id='ch', title='task', goal='goal', progress='', lifecycle='active',
                                   subscriptions={'qq:1': None}, rule_path='', version=4)
        async def observation(self, id):
            return SimpleNamespace(model_dump=lambda **kwargs: {'id': id})
        async def set_round_delivery(self, *args):
            self.delivery.append(args)
    class Client:
        def __init__(self):
            self.calls = []
        async def ensure_session(self, channel_id, request_id, session_key):
            assert session_key == 'slot-a'
            return 'session'
        async def wake(self, **kwargs):
            self.calls.append(kwargs)
            return {'task_id': 'task', 'status': 'accepted'}
    store, client = Store(), Client()
    plugin = BridgePlugin('icarus-bridge', store, client, 'http://argus', rule_files=SimpleNamespace())
    await plugin.deliver_channel('ch')
    await plugin.deliver_channel('ch')
    assert client.calls[0] == client.calls[1]
    assert client.calls[0]['binding_id'] == 'binding'
    assert store.round['delivered'] == []
    assert store.delivery == [('ch', 'wake', 'session', 'task')] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'cancelled', 'completed'])
@pytest.mark.parametrize('replay_safe', [True, False, None])
async def test_terminal_status_reconciles_only_explicitly_safe_wakes(status, replay_safe):
    class Store:
        calls = []
        async def begin_round(self, id):
            return {'wake_id': 'old-wake', 'task_id': 'old-task'}
        async def reconcile_round(self, *args):
            self.calls.append(args)
        async def mark_round_outcome_unknown(self, *args):
            assert args == ('channel', 'old-wake')
    class Client:
        async def status(self, wake):
            assert wake == 'old-wake'
            return {'task_id': 'old-task', 'status': status, **({'replay_safe': replay_safe} if replay_safe is not None else {})}
    store = Store()
    result = await BridgePlugin('icarus-bridge', store, Client(), 'http://argus').deliver_channel('channel')
    assert result['status'] == status
    assert store.calls == ([('channel', 'old-wake', status)] if replay_safe is True else [])
