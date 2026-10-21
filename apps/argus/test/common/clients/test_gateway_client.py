import asyncio
import json
import pytest


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
    def __aiter__(self):
        return self
    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value
    async def send(self, raw):
        self.sent.append(json.loads(raw))
        request = self.sent[-1]
        await self.incoming.put(json.dumps({'id': request['id'], 'result': {'echo': request['params']}}))
    async def close(self):
        await self.incoming.put(None)


async def test_rpc_correlates_concurrent_calls_and_closes_reader():
    from apps.argus.src.common.clients.gateway_client import IcarusClient
    socket = Socket()
    async def connect(*args, **kwargs):
        return socket
    client = IcarusClient('ws://127.0.0.1/rpc', connector=connect)
    results = await asyncio.gather(client.call('one', {'n': 1}), client.call('two', {'n': 2}))
    assert results == [{'echo': {'n': 1}}, {'echo': {'n': 2}}]
    assert len({request['id'] for request in socket.sent}) == 2
    await client.close()
    assert not client.connected


async def test_transport_failure_is_wrapped():
    from apps.argus.src.common.clients.gateway_client import IcarusClient, GatewayTransportError
    async def fail(*args, **kwargs):
        raise ConnectionError('synthetic')
    with pytest.raises(GatewayTransportError):
        await IcarusClient('ws://127.0.0.1/rpc', connector=fail).call('one', {})
