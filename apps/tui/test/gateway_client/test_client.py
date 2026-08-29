import asyncio
import json

from apps.tui.src.gateway_client import GatewayClient


class SocketStub:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        item = await self.incoming.get()
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    async def close(self):
        self.closed = True


async def respond(socket, result):
    while not socket.sent:
        await asyncio.sleep(0)
    request = socket.sent[-1]
    await socket.incoming.put(
        {"jsonrpc": "2.0", "id": request["id"], "result": result}
    )


def test_gateway_client建立session订阅并关联并发response():
    async def run():
        socket = SocketStub()

        async def connector(url):
            assert url == "ws://gateway/rpc"
            return socket

        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            connector=connector,
        )
        start = asyncio.create_task(client.start())
        await respond(
            socket,
            {"workspace_key": "workspace", "session_id": "session", "lifecycle": "ready"},
        )
        while len(socket.sent) < 2:
            await asyncio.sleep(0)
        subscribe = socket.sent[-1]
        await socket.incoming.put(
            {"jsonrpc": "2.0", "id": subscribe["id"], "result": {"subscribed": True}}
        )
        await start

        first = asyncio.create_task(client.request("one", {}))
        second = asyncio.create_task(client.request("two", {}))
        while len(socket.sent) < 4:
            await asyncio.sleep(0)
        one, two = socket.sent[-2:]
        await socket.incoming.put({"jsonrpc": "2.0", "id": two["id"], "result": 2})
        await socket.incoming.put({"jsonrpc": "2.0", "id": one["id"], "result": 1})
        result = await asyncio.gather(first, second)
        await client.close()
        return client, socket, result

    client, socket, result = asyncio.run(run())
    assert client.session_id == "session"
    assert client.workspace_key == "workspace"
    assert result == [1, 2]
    assert socket.closed is True


def test_gateway_client接收runtime_update_notification():
    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        client._socket = socket
        client._reader = asyncio.create_task(client._read_loop())
        subscription = client.subscribe_updates()
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "method": "runtime.update",
                "params": {
                    "workspace_key": "workspace",
                    "session_id": "session",
                    "task_id": "task",
                    "type": "task.started",
                    "payload": {},
                    "occurred_at": "2026-01-01T00:00:00Z",
                },
            }
        )
        update = await subscription.next_update()
        await client.close()
        return update

    assert asyncio.run(run()).type == "task.started"


def test_gateway_client断线唤醒pending请求和update订阅():
    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        client._socket = socket
        client._reader = asyncio.create_task(client._read_loop())
        subscription = client.subscribe_updates()
        request = asyncio.create_task(client.request("pending", {}))
        update = asyncio.create_task(subscription.next_update())
        while not socket.sent:
            await asyncio.sleep(0)
        await socket.incoming.put(ConnectionError("disconnected"))
        results = await asyncio.gather(request, update, return_exceptions=True)
        await client.close()
        return results

    results = asyncio.run(run())
    assert all(isinstance(item, ConnectionError) for item in results)
