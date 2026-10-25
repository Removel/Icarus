"""Service-only RPC contract, authentication and truthful execution state."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
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


SESSION = '30000000-0000-4000-8000-000000000001'


def v2_fixture():
    return json.loads((Path(__file__).parent / 'fixtures/argus_v2.json').read_text())


def service_params(**values):
    return {'caller_token': 'fixture-secret', 'protocol_version': 2, **values}


def execution_view(request):
    from agent_app.application.models.scoped_execution import ExecutionView
    return ExecutionView(wake_id=request.wake_id, origin_boot_id=request.origin_boot_id,
        execution_id=str(UUID(int=1)), session_id=SESSION, task_id=str(UUID(int=3)), scope=request.scope)


def test_v2_fixture_preserves_normalization_and_frozen_target_fingerprint():
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    from apps.gateway.src.protocol.argus_models import WakeParams
    fixture = v2_fixture()
    params = WakeParams.model_validate(service_params(**fixture['request']))
    request = ScopedWakeRequest.model_validate(params.model_dump(exclude={'caller_token'}))
    assert request.model_dump(mode='json') == fixture['request']
    assert request.fingerprint == fixture['fingerprint']
    target = 'ABCDEFAB-1234-4BCD-8DEF-0123456789AB'
    targeted = WakeParams.model_validate(service_params(**{**fixture['request'], 'target_session_id': target}))
    selected = ScopedWakeRequest.model_validate(targeted.model_dump(exclude={'caller_token'}))
    assert selected.target_session_id == 'abcdefab-1234-4bcd-8def-0123456789ab'
    assert selected.fingerprint != fixture['fingerprint']


@pytest.mark.parametrize('version', [True, False, '1', '2', 1.0, 2.0, 0, 3])
@pytest.mark.parametrize('method', ['wake', 'status', 'cancel', 'release', 'sessions', 'notify'])
def test_service_rejects_noninteger_or_unsupported_versions(method, version):
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    values = {'wake': v2_fixture()['request'], 'status': {'wake_id': WAKE},
        'cancel': {'wake_id': WAKE, 'reason': 'cleanup'}, 'release': {'wake_id': WAKE},
        'sessions': {'scope': wake_params()['scope']}, 'notify': v2_fixture()['notify']}
    params = service_params(**{**values[method], 'protocol_version': version})
    with pytest.raises(GatewayRpcError) as error:
        asyncio.run(ArgusMethods(object(), token_loader=lambda: 'fixture-secret').dispatch(
            f'argus.{method}', params, set()))
    assert error.value.code == INVALID_PARAMS


@pytest.mark.parametrize('version,include_target', [(1, True), (2, False)])
def test_wake_target_presence_is_versioned_even_when_null(version, include_target):
    from pydantic import ValidationError
    from apps.gateway.src.protocol.argus_models import WakeParams
    params = wake_params() | {'protocol_version': version}
    if include_target:
        params['target_session_id'] = None
    with pytest.raises(ValidationError):
        WakeParams.model_validate(params)


@pytest.mark.parametrize('method', ['wake', 'status', 'cancel', 'release', 'sessions', 'notify'])
@pytest.mark.parametrize('token', [None, 'wrong-secret', 1])
def test_every_argus_operation_authenticates_before_validation(method, token):
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    with pytest.raises(GatewayRpcError) as error:
        asyncio.run(ArgusMethods(object(), token_loader=lambda: 'fixture-secret').dispatch(
            f'argus.{method}', service_params(caller_token=token), set()))
    assert error.value.code == BUSINESS_ERROR
    assert error.value.data == {'code': 'unauthorized', 'retryable': False}
    assert 'wrong-secret' not in str(error.value)


@pytest.mark.parametrize('method,params', [
    ('sessions', {'protocol_version': 1, 'scope': wake_params()['scope']}),
    ('sessions', {'scope': wake_params()['scope'], 'limit': 0}),
    ('sessions', {'scope': wake_params()['scope'], 'limit': 21}),
    ('sessions', {'scope': wake_params()['scope'], 'limit': True}),
    ('sessions', {'scope': wake_params()['scope'], 'limit': '10'}),
    ('sessions', {'scope': wake_params()['scope'], 'cursor': 'x' * 2049}),
    ('sessions', {'scope': wake_params()['scope'], 'cursor': 1}),
    ('sessions', {'scope': {**wake_params()['scope'], 'external_id': '0'}}),
    ('sessions', {'scope': {**wake_params()['scope'], 'workspace_path': '/private'}}),
    ('sessions', {'scope': wake_params()['scope'], 'workspace_path': '/private'}),
    ('notify', {'protocol_version': 1, 'wake_id': WAKE, 'session_id': SESSION}),
    ('notify', {'wake_id': 'bad', 'session_id': SESSION}),
    ('notify', {'wake_id': WAKE, 'session_id': 'bad'}),
    ('notify', {'wake_id': WAKE}),
    *[('notify', {'wake_id': WAKE, 'session_id': SESSION, field: 'private-body'})
        for field in ('text', 'resources', 'binding_id', 'scope', 'callback_url', 'target_session_id')],
])
def test_v2_methods_reject_unsafe_or_unbounded_parameters(method, params):
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    with pytest.raises(GatewayRpcError) as error:
        asyncio.run(ArgusMethods(object(), token_loader=lambda: 'fixture-secret').dispatch(
            f'argus.{method}', service_params(**params), set()))
    assert error.value.code == INVALID_PARAMS
    assert 'private-body' not in str(error.value)


def test_sessions_maps_internal_page_and_notify_forwards_only_the_public_facades():
    from agent_app.argus_protocol import Scope
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    fixture = v2_fixture()
    class Runtime:
        notified = False
        async def scoped_sessions(self, scope, *, cursor=None, limit=10):
            assert isinstance(scope, Scope) and scope.model_dump() == fixture['request']['scope']
            if cursor is None:
                assert limit == 10
            else:
                assert cursor == 'page-two' and limit == 20
            return {'items': deepcopy(fixture['sessions']['sessions']), 'next_cursor': None}
        async def scoped_notify(self, *, wake_id, session_id):
            assert (wake_id, session_id) == (WAKE, SESSION)
            merged, self.notified = self.notified, True
            return {'accepted': True, 'merged': merged}
        async def scoped_submit(self, *args, **kwargs):
            raise AssertionError('Notification must not submit')
        async def submit(self, *args, **kwargs):
            raise AssertionError('Notification must not submit')
        async def steer_task(self, *args, **kwargs):
            raise AssertionError('Notification must not steer')
        async def cancel_task(self, *args, **kwargs):
            raise AssertionError('Notification must not cancel')
        async def scoped_cancel(self, *args, **kwargs):
            raise AssertionError('Notification must not cancel')
    async def run():
        methods = ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret')
        for values in ({}, {'cursor': 'page-two', 'limit': 20}):
            assert await methods.dispatch('argus.sessions', service_params(
                scope=fixture['request']['scope'], **values), set()) == fixture['sessions']
        assert await methods.dispatch('argus.notify', service_params(**fixture['notify']), set()) == {
            'protocol_version': 2, 'accepted': True, 'merged': False}
        assert await methods.dispatch('argus.notify', service_params(**fixture['notify']), set()) == {
            'protocol_version': 2, 'accepted': True, 'merged': True}
    asyncio.run(run())


def test_v2_wake_forwards_validated_target_without_token_or_fingerprint_changes():
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    class Runtime:
        async def scoped_submit(self, request):
            assert request.protocol_version == 2
            assert request.target_session_id == SESSION
            assert 'caller_token' not in request.model_dump()
            return execution_view(request)
    async def run():
        params = service_params(**{**v2_fixture()['request'], 'target_session_id': SESSION})
        result = await ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret').dispatch('argus.wake', params, set())
        assert result['protocol_version'] == 2 and result['accepted'] is True
        assert result['execution']['session_id'] == SESSION
    asyncio.run(run())


@pytest.mark.parametrize('version', [1, 2])
def test_cleanup_envelopes_follow_request_version_without_rewriting_legacy_wakes(version):
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    view = execution_view(ScopedWakeRequest.model_validate(v2_fixture()['request']))
    class Runtime:
        async def scoped_status(self, *, wake_id=None, scope=None):
            return view if wake_id == WAKE else None
        async def scoped_cancel(self, wake_id, reason):
            assert (wake_id, reason) == (WAKE, 'cleanup')
            return view
        async def scoped_release(self, wake_id):
            assert wake_id == WAKE
            return view
    async def run():
        methods = ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret')
        for method, extra, flag in [('status', {}, 'found'), ('cancel', {'reason': 'cleanup'}, 'accepted'), ('release', {}, 'released')]:
            result = await methods.dispatch(f'argus.{method}', service_params(
                protocol_version=version, wake_id=WAKE, **extra), set())
            assert result == {'protocol_version': version, flag: True, 'execution': view.model_dump(mode='json')}
        result = await methods.dispatch('argus.status', service_params(protocol_version=version,
            wake_id=str(UUID(int=999))), set())
        assert result == {'protocol_version': version, 'found': False, 'execution': None}
    asyncio.run(run())


@pytest.mark.parametrize('method,code', [
    ('wake', 'target_unavailable'), ('wake', 'scope_mismatch'), ('wake', 'not_found'),
    ('sessions', 'unavailable'), ('notify', 'unavailable'), ('notify', 'not_found'),
    ('notify', 'scope_busy'), ('status', 'unavailable'),
])
def test_facade_business_errors_do_not_become_success_or_status_not_found(method, code):
    from agent_app.application.models.scoped_execution import ScopedExecutionError
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    class Runtime:
        async def fail(self, *args, **kwargs):
            raise ScopedExecutionError(code)
        scoped_submit = scoped_sessions = scoped_notify = scoped_status = fail
    values = {'wake': v2_fixture()['request'], 'sessions': {'scope': wake_params()['scope']},
        'notify': v2_fixture()['notify'], 'status': {'wake_id': WAKE}}
    with pytest.raises(GatewayRpcError) as error:
        asyncio.run(ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret').dispatch(
            f'argus.{method}', service_params(**values[method]), set()))
    assert error.value.code == BUSINESS_ERROR
    assert error.value.data == {'code': code, 'retryable': code in {'unavailable', 'scope_busy'}}


def test_notify_unexpected_errors_and_logs_do_not_expose_token_or_body(caplog):
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    from apps.gateway.src.protocol.errors import INTERNAL_ERROR
    class Runtime:
        async def scoped_notify(self, **kwargs):
            raise RuntimeError('fixture-secret private-body')
    with pytest.raises(GatewayRpcError) as error:
        asyncio.run(ArgusMethods(Runtime(), token_loader=lambda: 'fixture-secret').dispatch(
            'argus.notify', service_params(**v2_fixture()['notify']), set()))
    assert error.value.code == INTERNAL_ERROR
    assert 'fixture-secret' not in str(error.value) + caplog.text
    assert 'private-body' not in str(error.value) + caplog.text


def test_real_agent_facades_scope_coalescing_target_validation_and_background_guards(tmp_path):
    from agent_app.application import AgentRuntime, SessionStore
    from agent_app.application.models.scoped_execution import ScopedWakeRequest
    from agent_app.config import ConfigModel
    from icarus.core.identity import SessionIdentity
    from apps.gateway.src.protocol.argus_methods import ArgusMethods
    from apps.gateway.src.protocol.methods import GatewayMethods
    class Client:
        async def session_pending(self, session_id, scope):
            return {'pending_count': 1, 'last_related_at': None}
        async def bind(self, *args):
            raise AssertionError('Invalid target must not bind or launch')
        async def aclose(self):
            pass
    def no_session(*args, **kwargs):
        raise AssertionError('Candidates and notification must not load a session runtime')
    async def run():
        workspace = tmp_path / 'workspace'
        config = ConfigModel(openai_base_url='http://127.0.0.1:1', anthropic_base_url='http://127.0.0.1:1',
            icarus_data_dir=tmp_path / 'data', argus={'enabled': True, 'workspace': workspace},
            model_settings={name: {'model_name': 'offline', 'context_window': 128000, 'max_tokens': 100,
                'temperature': 0, 'default_think_level': 'low'} for name in ('thinking', 'perception')})
        store = SessionStore(tmp_path / 'data')
        runtime = AgentRuntime(config_loader=lambda: config, session_store=store,
            handoff_client=Client(), session_factory=no_session)
        await runtime.start()
        try:
            request = ScopedWakeRequest.model_validate(v2_fixture()['request'])
            identity = SessionIdentity.create(workspace, SESSION)
            await store.reserve_scoped(request, identity=identity, execution_id=str(UUID(int=101)), task_id=str(UUID(int=102)))
            interactive = str(UUID(int=103))
            await runtime.create_session(workspace, interactive, load_runtime=False)
            methods = ArgusMethods(runtime, token_loader=lambda: 'fixture-secret')
            page = await methods.dispatch('argus.sessions', service_params(scope=request.scope.model_dump()), set())
            assert set(page) == {'protocol_version', 'sessions', 'next_cursor'}
            assert len(page['sessions']) == 1 and page['sessions'][0]['session_id'] == SESSION
            assert page['sessions'][0]['has_work'] is True
            foreign = {**request.scope.model_dump(), 'external_id': '43'}
            assert await methods.dispatch('argus.sessions', service_params(scope=foreign), set()) == {
                'protocol_version': 2, 'sessions': [], 'next_cursor': None}
            for merged in (False, True):
                assert await methods.dispatch('argus.notify', service_params(**v2_fixture()['notify']), set()) == {
                    'protocol_version': 2, 'accepted': True, 'merged': merged}
            for session_id in (interactive, str(UUID(int=999))):
                with pytest.raises(GatewayRpcError) as error:
                    await methods.dispatch('argus.notify', service_params(wake_id=WAKE, session_id=session_id), set())
                assert error.value.data['code'] == 'not_found'
            for target, scope in ((interactive, request.scope.model_dump()), (str(UUID(int=999)), request.scope.model_dump()), (SESSION, foreign)):
                invalid = deepcopy(v2_fixture()['request'])
                invalid.update(wake_id=str(UUID(int=104)), target_session_id=target, scope=scope)
                invalid['items'][0]['chat_id'] = f"qq:7:group:{scope['external_id']}"
                invalid['items'][0]['locator']['external_id'] = scope['external_id']
                with pytest.raises(GatewayRpcError) as error:
                    await methods.dispatch('argus.wake', service_params(**invalid), set())
                assert error.value.data['code'] == 'target_unavailable'
            ordinary = GatewayMethods(runtime)
            for method, extra in [('session.get', {}), ('session.get_history', {}),
                    ('session.steer', {'task_id': str(UUID(int=102)), 'prompt': 'do not inject'}),
                    ('session.cancel', {'task_id': str(UUID(int=102))}),
                    ('session.subscribe', {'workspace_key': identity.workspace_key})]:
                params = {'workspace_key': identity.workspace_key, 'session_id': SESSION} if method == 'session.subscribe' else {
                    'workspace_path': str(workspace), 'session_id': SESSION}
                with pytest.raises(GatewayRpcError) as error:
                    await ordinary.dispatch(method, {**params, **extra}, set())
                assert error.value.data['code'] == 'session_not_found'
        finally:
            await runtime.stop()
    asyncio.run(run())
