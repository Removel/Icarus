import asyncio
import json

from apps.argus.src.clients.icarus_client import IcarusClient, GatewayTransportError
from websockets.exceptions import ConnectionClosed


class FakeSocket:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.sent = []

    async def send(self, message):
        request = json.loads(message)
        self.sent.append(request)
        await self.inbox.put(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                         "result": {"session_id": "s", "workspace_key": "key"}}))

    async def close(self):
        await self.inbox.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_closed_socket_during_send_becomes_transport_uncertainty():
    async def exercise():
        socket = FakeSocket()
        async def connector(url, *, max_size):
            return socket
        client = IcarusClient("ws://127.0.0.1:8765/rpc", connector=connector)
        await client.connect()
        async def closed_send(_message):
            raise ConnectionClosed(None, None)
        socket.send = closed_send
        try:
            try:
                await client.session_submit("/work", "s", "prompt", "submission-1")
            except GatewayTransportError:
                pass
            else:
                raise AssertionError("Closed WebSocket must be a transport-unknown result")
        finally:
            await client.close()
    asyncio.run(exercise())


def test_gateway_client_reconnects_for_next_safe_call_after_disconnection():
    async def exercise():
        first, second = FakeSocket(), FakeSocket()
        sockets = iter((first, second))

        async def connector(url, *, max_size):
            return next(sockets)

        client = IcarusClient("ws://127.0.0.1:8765/rpc", connector=connector)
        await client.connect()
        assert (await client.session_get("/work", "s"))["workspace_key"] == "key"
        await first.close()
        await client._reader
        assert (await client.session_get("/work", "s"))["workspace_key"] == "key"
        assert [request["method"] for request in second.sent] == ["session.get"]
        await client.close()

    asyncio.run(exercise())
