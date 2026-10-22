"""Service-only RPC contract, authentication and truthful execution state."""

import asyncio
from copy import deepcopy
from uuid import UUID

import pytest

from apps.gateway.src.protocol.errors import GatewayRpcError, INVALID_PARAMS, BUSINESS_ERROR, METHOD_NOT_FOUND


WAKE = '10000000-0000-4000-8000-000000000001'
BOOT = '20000000-0000-4000-8000-000000000001'


def wake_params():
    return {'caller_token': 'fixture-secret', 'protocol_version': 1, 'wake_id': WAKE, 'origin_boot_id': BOOT,
        'scope': {'source': 'qq', 'account_id': '7', 'chat_kind': 'group', 'external_id': '42'},
        'reason': 'question', 'rule_id': None, 'items': [{
            'observation_id': '60000000-0000-4000-8000-000000000001', 'source': 'qq', 'chat_id': 'qq:7:group:42',
            'message_id': '88', 'revision': 1, 'locator': {'account_id': '7', 'chat_kind': 'group',
                'external_id': '42', 'message_id': '88', 'time': 10}, 'excerpt': 'question', 'truncated': False}]}


def test_frozen_wire_fixture_uses_the_same_normalized_wake_boundary():
    import json
    from pathlib import Path
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    from apps.gateway.src.protocol.argus_models import WakeParams
    fixture = json.loads((Path(__file__).parent/'fixtures/argus_v1.json').read_text())
    params = WakeParams.model_validate({'caller_token':'fixture-secret', **fixture['request']})
    request = ScopedWakeRequest.model_validate(params.model_dump(exclude={'caller_token'}))
    assert request.fingerprint == fixture['fingerprint']


def test_four_methods_require_auth_and_integer_protocol_version():
    async def run():
        from apps.gateway.src.protocol.argus_methods import ArgusMethods
        methods = ArgusMethods(object(), token_loader=lambda: 'fixture-secret')
        for name in ('wake', 'status', 'cancel', 'release'):
            with pytest.raises(GatewayRpcError) as error:
                await methods.dispatch(f'argus.{name}', {'caller_token': 'wrong-secret', 'protocol_version': 1}, set())
            assert error.value.code == BUSINESS_ERROR and error.value.data['code'] == 'unauthorized'
            assert 'wrong-secret' not in str(error.value)
            with pytest.raises(GatewayRpcError) as error:
                await methods.dispatch(f'argus.{name}', {'caller_token': 'fixture-secret', 'protocol_version': '1'}, set())
            assert error.value.code == INVALID_PARAMS
        with pytest.raises(GatewayRpcError) as error:
            await methods.dispatch('session.create', {}, set())
        assert error.value.code == METHOD_NOT_FOUND
    asyncio.run(run())


def test_status_not_found_and_wake_token_removal():
    async def run():
        from apps.gateway.src.protocol.argus_methods import ArgusMethods
        from agent_app.application.models.scoped_execution import ExecutionView
        seen = []
        class Runtime:
            async def scoped_submit(self, request):
                seen.append(request.model_dump())
                return ExecutionView(wake_id=request.wake_id, origin_boot_id=request.origin_boot_id,
                    execution_id=str(UUID(int=1)), session_id=str(UUID(int=2)), task_id=str(UUID(int=3)), scope=request.scope)
            async def scoped_status(self, **kwargs): return None
        methods = ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret')
        result = await methods.dispatch('argus.wake', wake_params(), set())
        assert result['accepted'] is True
        assert result['execution']['release_complete'] is False
        assert result['execution']['exit_kind'] is None
        assert 'caller_token' not in seen[0]
        assert await methods.dispatch('argus.status', {'caller_token': 'fixture-secret', 'protocol_version': 1,
            'wake_id': WAKE}, set()) == {'protocol_version': 1, 'found': False, 'execution': None}
        bad = wake_params()
        bad['items'][0]['locator']['external_id'] = '43'
        with pytest.raises(GatewayRpcError) as error:
            await methods.dispatch('argus.wake', bad, set())
        assert error.value.code == INVALID_PARAMS and len(seen) == 1
    asyncio.run(run())


def test_ordinary_subscribe_checks_public_guard_before_registering():
    async def run():
        from apps.gateway.src.protocol.methods import GatewayMethods
        from agent_app.application.errors import SessionNotFoundError
        class Runtime:
            async def ensure_public_session(self, *, workspace_key, session_id):
                raise SessionNotFoundError(session_id)
        subscriptions = set()
        with pytest.raises(GatewayRpcError) as error:
            await GatewayMethods(Runtime()).dispatch('session.subscribe', {'workspace_key': 'known', 'session_id': 'background'}, subscriptions)
        assert error.value.data['code'] == 'session_not_found'
        assert subscriptions == set()
    asyncio.run(run())
