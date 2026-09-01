import asyncio
import json

import pytest

from apps.tui.src.gateway_client import GatewayClient, GatewayClientError


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


def test_gateway_client读取session历史并保留缓冲实时update():
    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            session_id="session",
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        client._socket = socket
        client._reader = asyncio.create_task(client._read_loop())
        subscription = client.subscribe_updates()
        history_task = asyncio.create_task(client.get_session_history())
        while not socket.sent:
            await asyncio.sleep(0)
        request = socket.sent[-1]
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
                    "occurred_at": "2026-01-01T00:00:01Z",
                    "sequence": 2,
                },
            }
        )
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "records": [
                        {
                            "workspace_key": "workspace",
                            "session_id": "session",
                            "task_id": "task",
                            "type": "user.message",
                            "payload": {"text": "hello", "resources": []},
                            "occurred_at": "2026-01-01T00:00:00Z",
                            "sequence": 1,
                        }
                    ],
                    "history_cursor": 1,
                },
            }
        )
        history = await history_task
        live = await subscription.next_update()
        await client.close()
        return history, live

    history, live = asyncio.run(run())
    assert history.history_cursor == 1
    assert history.records[0].type == "user.message"
    assert live.sequence == 2


def test_gateway_client_existing_only不会创建缺失session():
    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            session_id="missing",
            create_if_missing=False,
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        start = asyncio.create_task(client.start())
        while not socket.sent:
            await asyncio.sleep(0)
        request = socket.sent[-1]
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32000,
                    "message": "Session is not found",
                    "data": {"code": "session_not_found"},
                },
            }
        )
        with pytest.raises(GatewayClientError) as error_info:
            await start
        return client, socket, error_info.value

    client, socket, error = asyncio.run(run())
    assert error.code == "session_not_found"
    assert [item["method"] for item in socket.sent] == ["session.get"]
    assert socket.closed is True
    assert client.session_id == "missing"


def test_gateway_client新建后订阅失败会请求清理空session():
    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            session_id="new-session",
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        start = asyncio.create_task(client.start())

        while len(socket.sent) < 1:
            await asyncio.sleep(0)
        get_request = socket.sent[-1]
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": get_request["id"],
                "error": {
                    "code": -32000,
                    "message": "not found",
                    "data": {"code": "session_not_found"},
                },
            }
        )
        while len(socket.sent) < 2:
            await asyncio.sleep(0)
        create_request = socket.sent[-1]
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": create_request["id"],
                "result": {
                    "workspace_key": "workspace",
                    "session_id": "new-session",
                    "lifecycle": "ready",
                },
            }
        )
        while len(socket.sent) < 3:
            await asyncio.sleep(0)
        subscribe_request = socket.sent[-1]
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": subscribe_request["id"],
                "error": {
                    "code": -32603,
                    "message": "subscribe failed",
                },
            }
        )
        while len(socket.sent) < 4:
            await asyncio.sleep(0)
        discard_request = socket.sent[-1]
        await socket.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": discard_request["id"],
                "result": {
                    "workspace_key": "workspace",
                    "session_id": "new-session",
                    "status": "discarded",
                },
            }
        )
        with pytest.raises(GatewayClientError):
            await start
        return socket

    socket = asyncio.run(run())
    assert [item["method"] for item in socket.sent] == [
        "session.get",
        "session.create",
        "session.subscribe",
        "session.discard_empty",
    ]
    assert socket.closed is True


def test_gateway_client列出查询并清理指定session():
    async def request_and_respond(socket, coroutine, result):
        sent_count = len(socket.sent)
        task = asyncio.create_task(coroutine)
        while len(socket.sent) == sent_count:
            await asyncio.sleep(0)
        request = socket.sent[-1]
        await socket.incoming.put(
            {"jsonrpc": "2.0", "id": request["id"], "result": result}
        )
        return request, await task

    async def run():
        socket = SocketStub()
        client = GatewayClient(
            url="ws://gateway/rpc",
            workspace_path="/workspace",
            session_id="current",
            connector=lambda url: asyncio.sleep(0, result=socket),
        )
        client._socket = socket
        client._reader = asyncio.create_task(client._read_loop())

        list_request, sessions = await request_and_respond(
            socket,
            client.list_sessions(),
            {
                "sessions": [
                    {
                        "session_id": "old",
                        "first_user_input": "hello",
                    }
                ]
            },
        )
        status_request, status = await request_and_respond(
            socket,
            client.get_session_status(),
            {
                "workspace_key": "workspace",
                "session_id": "current",
                "lifecycle": "ready",
            },
        )
        discard_request, discarded = await request_and_respond(
            socket,
            client.discard_empty_session("old-empty"),
            {
                "workspace_key": "workspace",
                "session_id": "old-empty",
                "status": "discarded",
            },
        )
        await client.close()
        return (
            list_request,
            sessions,
            status_request,
            status,
            discard_request,
            discarded,
        )

    (
        list_request,
        sessions,
        status_request,
        status,
        discard_request,
        discarded,
    ) = asyncio.run(run())
    assert list_request == {
        **{key: list_request[key] for key in ("jsonrpc", "id")},
        "method": "session.list",
        "params": {"workspace_path": "/workspace"},
    }
    assert sessions[0].session_id == "old"
    assert sessions[0].first_user_input == "hello"
    assert status_request["method"] == "session.get"
    assert status_request["params"]["session_id"] == "current"
    assert status["lifecycle"] == "ready"
    assert discard_request["method"] == "session.discard_empty"
    assert discard_request["params"]["session_id"] == "old-empty"
    assert discarded.status == "discarded"
