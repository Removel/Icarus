import asyncio
import hashlib
import hmac
import json
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from apps.argus.src.core.config import ArgusConfig, QQConfig


def body(message_id=789, text='hello', private=True):
    return json.dumps({'self_id': 123, 'post_type': 'message', 'message_type': 'private' if private else 'group',
        'message_id': message_id, 'group_id': 999, 'user_id': 456, 'sender': {'nickname': 'peer'},
        'time': 1700000000, 'message': text}).encode()


def headers(raw):
    return {'X-Self-ID': '123', 'X-Signature': 'sha1=' + hmac.new(b'test-secret', raw, hashlib.sha1).hexdigest()}


async def test_gateway_composition_routes_v2_and_mounts_service_pending(database, monkeypatch):
    from uuid import uuid4
    from apps.argus.src.main import create_app, create_service_app
    from apps.argus.src.common.schemas.wake import WakeReceipt
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'fixture-data')
    calls = []
    class Agent:
        async def status(self, **kwargs): return None
        async def sessions(self, scope, **kwargs):
            return {'protocol_version': 2, 'sessions': [], 'next_cursor': None}
        async def submit(self, payload):
            calls.append(payload)
            return WakeReceipt(accepted=True)
    app = create_app(ArgusConfig(execution={'mode': 'gateway'}, qq=QQConfig(enabled=True, bot_id='123'),
        scan_interval_seconds=.01), database=database, agent=Agent(), qq_secret='test-secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            raw = body()
            assert (await client.post('/api/argus/qq/onebot/events', content=raw, headers=headers(raw))).status_code == 204
            async with asyncio.timeout(2):
                while not calls:
                    await asyncio.sleep(.01)
            assert calls[0].protocol_version == 2 and calls[0].target_session_id is None
        service = create_service_app(app)
        async with AsyncClient(transport=ASGITransport(app=service), base_url='http://test') as client:
            response = await client.post(f'/api/argus/internal/sessions/{uuid4()}/pending', json={
                'protocol_version': 2, 'scope': {'source': 'qq', 'account_id': '123', 'chat_kind': 'private', 'external_id': '456'}},
                headers={'Authorization': 'Bearer fixture-data'})
            assert response.status_code == 200 and response.json()['pending_count'] == 0


async def test_http_private_flow_resolves_with_mock(database):
    from apps.argus.src.main import create_app
    config = ArgusConfig(qq=QQConfig(enabled=True, bot_id='123'), scan_interval_seconds=.01)
    app = create_app(config, database=database, qq_secret='test-secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            for path in ('/health', '/api/argus/health'):
                assert (await client.get(path)).json() == {'status': 'ready'}
            raw = body()
            assert (await client.post('/api/argus/qq/onebot/events', content=raw, headers=headers(raw))).status_code == 204
            async with asyncio.timeout(3):
                while True:
                    response = await client.get('/api/argus/wake-requests')
                    values = response.json()
                    if values and values[0]['status'] == 'resolved':
                        break
                    await asyncio.sleep(.01)
            assert values[0]['active'] is False and values[0]['outcome']['simulated'] is True
            detail = (await client.get('/api/argus/wake-requests/' + values[0]['id'])).json()
            assert detail['items'][0]['status'] == 'processed'
            assert (await client.get('/api/argus/wake-requests?status=nonsense')).status_code == 422


async def test_bad_signature_and_account_do_not_insert(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(qq=QQConfig(enabled=True, bot_id='123')),
                     database=database, qq_secret='test-secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            raw = body()
            assert (await client.post('/api/argus/qq/onebot/events', content=raw,
                                      headers={'X-Signature': 'sha1=bad'})).status_code == 401
            wrong = {**headers(raw), 'X-Self-ID': '999'}
            assert (await client.post('/api/argus/qq/onebot/events', content=raw, headers=wrong)).status_code == 403
            assert await app.state.repository.chats_to_scan() == ()


async def test_new_lifespan_does_not_keep_previous_boot_routes(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(qq=QQConfig(enabled=True, bot_id='123')), database=database, qq_secret='test-secret')
    async with app.router.lifespan_context(app):
        first_boot = app.state.repository.boot_id
    async with app.router.lifespan_context(app):
        assert app.state.repository.boot_id != first_boot
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            raw = body(999)
            assert (await client.post('/api/argus/qq/onebot/events', content=raw, headers=headers(raw))).status_code == 204
            assert len(await app.state.repository.latest_open_items('qq:123:private:456', limit=20)) == 1


async def test_ingress_does_not_wait_for_blocked_semantic_judgement(database):
    from apps.argus.src.main import create_app
    entered, release = asyncio.Event(), asyncio.Event()
    class BlockedJev:
        async def ask(self, **kwargs):
            entered.set()
            await release.wait()
            raise ValueError('synthetic cancelled judgement')
    app = create_app(ArgusConfig(qq=QQConfig(enabled=True, bot_id='123'),
        scan_interval_seconds=.01, quiet_seconds=.01), database=database,
        jev=BlockedJev(), qq_secret='test-secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            raw = body(1001, '群里的普通问题', private=False)
            assert (await client.post('/api/argus/qq/onebot/events', content=raw, headers=headers(raw))).status_code == 204
            await asyncio.wait_for(entered.wait(), 1)
            later = body(1002, '后来收到的消息', private=False)
            response = await asyncio.wait_for(client.post('/api/argus/qq/onebot/events',
                content=later, headers=headers(later)), .5)
            assert response.status_code == 204
            assert len(await app.state.repository.latest_open_items('qq:123:group:999', limit=20)) == 2
            release.set()


async def test_failing_client_close_does_not_skip_database_cleanup(monkeypatch):
    from apps.argus.src import main
    from pydantic import SecretStr
    events = []
    class OwnedDatabase:
        def __init__(self, dsn):
            pass
        async def start(self):
            events.append('db_start')
        async def close(self):
            events.append('db_close')
    class BrokenClose:
        async def aclose(self):
            events.append('client_close')
            raise RuntimeError('synthetic close failure')
    monkeypatch.setattr(main, 'Database', OwnedDatabase)
    monkeypatch.setattr(main, 'MockAgentClient', lambda **kwargs: BrokenClose())
    monkeypatch.setattr(main.ObservationScheduler, 'run', lambda self: asyncio.Event().wait())
    app = main.create_app(ArgusConfig(database_url=SecretStr('postgresql+asyncpg://test/argus_test'),
        qq=QQConfig(enabled=True, bot_id='123')), qq_secret='secret')
    import pytest
    with pytest.raises(RuntimeError, match='synthetic close failure'):
        async with app.router.lifespan_context(app):
            pass
    assert events == ['db_start', 'client_close', 'db_close']


async def test_startup_failure_closes_owned_database(monkeypatch):
    from apps.argus.src import main
    from pydantic import SecretStr
    events = []
    class BrokenDatabase:
        def __init__(self, dsn):
            pass
        async def start(self):
            events.append('start')
            raise RuntimeError('synthetic unavailable')
        async def close(self):
            events.append('close')
    monkeypatch.setattr(main, 'Database', BrokenDatabase)
    app = main.create_app(ArgusConfig(database_url=SecretStr('postgresql+asyncpg://test/argus_test')))
    import pytest
    with pytest.raises(RuntimeError, match='synthetic unavailable'):
        async with app.router.lifespan_context(app):
            raise AssertionError('must not become ready')
    assert events == ['start', 'close']
    assert app.state.ready is False and app.state.accepting is False


async def test_disabled_source_and_dead_scheduler_health(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(), database=database)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/health')).status_code == 200
            assert (await client.post('/api/argus/qq/onebot/events')).status_code == 404
            assert app.state.scheduler_task is None
