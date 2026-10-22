"""The real adapter preserves business identity and never treats unavailable as empty."""

from uuid import uuid4

import pytest

from apps.argus.test.factories import payload_factory


async def test_real_adapter_sends_fixed_wire_and_status_has_explicit_not_found():
    from apps.argus.src.common.clients.gateway_agent_client import GatewayAgentClient
    calls = []
    class Transport:
        async def call(self, method, params):
            calls.append((method, params))
            if method == 'argus.status':
                return {'protocol_version': 1, 'found': False, 'execution': None}
            return {'protocol_version': 1, 'accepted': True, 'execution': {'execution_id': 'execution'}}
        async def close(self): pass
    payload = payload_factory()
    client = GatewayAgentClient('ws://127.0.0.1:8765/argus/rpc', 'fixture-token', transport=Transport())
    receipt = await client.submit(payload)
    assert receipt.accepted
    assert await client.status(wake_id=payload.wake_id) is None
    method, request = calls[0]
    assert method == 'argus.wake'
    assert request['caller_token'] == 'fixture-token' and request['protocol_version'] == 1
    assert request['origin_boot_id'] == str(payload.boot_id)
    assert request['scope'] == {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'}
    assert request['items'][0]['observation_id'] == str(payload.items[0].item_id)
    assert 'workspace_path' not in request


async def test_real_adapter_preserves_busy_and_unavailable_as_business_errors():
    from apps.argus.src.common.clients.gateway_agent_client import GatewayAgentClient
    from apps.argus.src.common.clients.gateway_client import GatewayBusinessError
    class Transport:
        async def call(self, *args):
            raise GatewayBusinessError('unavailable', 'Unavailable', data={'code': 'unavailable', 'retryable': True})
        async def close(self): pass
    client = GatewayAgentClient('ws://127.0.0.1:8765/argus/rpc', 'fixture-token', transport=Transport())
    with pytest.raises(GatewayBusinessError) as error:
        await client.status(wake_id=uuid4())
    assert error.value.retryable


def test_frozen_wire_fixture_matches_argus_internal_payload_fingerprint():
    from pathlib import Path
    import json
    from apps.argus.src.common.schemas.wake import WakePayload, WakeItem, request_fingerprint, wake_wire
    value = json.loads((Path(__file__).parents[1]/'fixtures/argus_v1.json').read_text())
    request = value['request']
    item = dict(request['items'][0])
    item['item_id'] = item.pop('observation_id')
    payload = WakePayload(wake_id=request['wake_id'], boot_id=request['origin_boot_id'], chat_id='qq:7:group:42',
        reason='question', rule_id=None, items=(WakeItem.model_validate(item),))
    assert request_fingerprint(payload) == value['fingerprint']
    assert wake_wire(payload) == request
