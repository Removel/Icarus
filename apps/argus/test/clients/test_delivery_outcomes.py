import asyncio
from apps.argus.src.clients.icarus_client import IcarusClient, GatewayTransportError


def test_steer_rejection_is_typed_and_not_retried():
    async def exercise():
        client = IcarusClient('ws://unused/rpc')
        calls = []
        async def call(method, params):
            calls.append((method,params))
            return {'status':'already_finished','task_id':'t'}
        client.call = call
        result = await client.session_steer('/work','s','t','more','b:steer:t')
        assert result.kind == 'rejected' and result.task_id == 't'
        assert len(calls) == 1
    asyncio.run(exercise())


def test_unknown_transport_is_not_a_rejection():
    async def exercise():
        client = IcarusClient('ws://unused/rpc')
        async def call(method,params):
            raise GatewayTransportError('offline')
        client.call = call
        result = await client.session_submit('/work','s','more','operation')
        assert result.kind == 'unknown'
    asyncio.run(exercise())


def test_accepted_steer_with_different_target_is_unknown():
    async def exercise():
        client = IcarusClient('ws://unused/rpc')
        async def call(method,params):
            return {'status':'accepted','task_id':'other'}
        client.call = call
        result = await client.session_steer('/work','s','target','more','operation')
        assert result.kind == 'unknown' and result.error_type == 'invalid_receipt'
    asyncio.run(exercise())


def test_ensure_session_reuses_id_after_lost_create_response():
    from apps.argus.src.clients.icarus_client import GatewayBusinessError
    async def exercise():
        client = IcarusClient('ws://unused/rpc')
        created, calls = False, []
        async def call(method,params):
            nonlocal created
            calls.append((method,params['session_id']))
            if method == 'session.get' and not created:
                raise GatewayBusinessError('session_not_found','missing')
            if method == 'session.create':
                created = True
                raise GatewayTransportError('response lost')
            return {'session_id':'stable','workspace_key':'key','lifecycle':'ready','active_task_ids':[]}
        client.call = call
        result = await client.ensure_session('/work','stable')
        assert result['session_id'] == 'stable'
        assert calls == [('session.get','stable'),('session.create','stable'),('session.get','stable')]
    asyncio.run(exercise())
