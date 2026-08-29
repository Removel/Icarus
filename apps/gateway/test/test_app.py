import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.agent.src.application.runtime_update_stream import RuntimeUpdateStream
from apps.agent.src.runtime_update import RuntimeUpdate
from apps.gateway.src.app import create_app


class RuntimeStub:
    def __init__(self):
        self.is_running = False
        self.stream = RuntimeUpdateStream()
        self.started = 0
        self.stopped = 0

    async def start(self):
        self.started += 1
        self.is_running = True

    async def stop(self):
        self.stopped += 1
        self.is_running = False
        self.stream.close()

    def subscribe_updates(self):
        return self.stream.subscribe()

    def get_session_status(self, workspace_path, session_id):
        del workspace_path
        return {
            "workspace_key": "workspace",
            "session_id": session_id,
            "lifecycle": "ready",
        }


def request(method, params, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def test_gateway_lifespan_health和jsonrpc():
    runtime = RuntimeStub()
    with TestClient(create_app(runtime)) as client:
        assert client.get("/health").json() == {"status": "ready"}
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_json(request("runtime.get_status", {}))
            assert websocket.receive_json() == {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"status": "ready"},
            }
            websocket.send_text("{")
            error = websocket.receive_json()
            assert error["error"]["code"] == -32700
    assert runtime.started == 1
    assert runtime.stopped == 1


def test_gateway按连接关注session过滤runtime_update():
    runtime = RuntimeStub()
    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_json(
                request(
                    "session.subscribe",
                    {"workspace_key": "workspace", "session_id": "a"},
                )
            )
            assert websocket.receive_json()["result"] == {"subscribed": True}
            client.portal.call(
                runtime.stream.publish,
                RuntimeUpdate(
                    workspace_key="workspace",
                    session_id="b",
                    task_id="ignored",
                    type="task.started",
                    payload={},
                    occurred_at=datetime.now(UTC),
                ),
            )
            client.portal.call(
                runtime.stream.publish,
                RuntimeUpdate(
                    workspace_key="workspace",
                    session_id="a",
                    task_id="task",
                    type="assistant.text_delta",
                    payload={"step": 1, "text": "hello"},
                    occurred_at=datetime.now(UTC),
                ),
            )
            notification = websocket.receive_json()
            assert notification["method"] == "runtime.update"
            assert notification["params"]["session_id"] == "a"
            assert notification["params"]["payload"]["text"] == "hello"


def test_jsonrpc_notification失败不返回error_response():
    runtime = RuntimeStub()
    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/rpc") as websocket:
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "unknown.notification",
                    "params": {},
                }
            )
            websocket.send_json(request("runtime.get_status", {}, 2))
            response = websocket.receive_json()
            assert response["id"] == 2
            assert response["result"] == {"status": "ready"}
