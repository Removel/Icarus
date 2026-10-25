import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from agent_app.application.runtime_update_stream import RuntimeUpdateStream
from icarus.core.runtime_update import RuntimeUpdate
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

    async def ensure_public_session(self, *, workspace_key, session_id):
        pass

    def subscribe_updates(self):
        return self.stream.subscribe()

    async def get_session_status(self, workspace_path, session_id):
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
                    type="assistant.thinking_delta",
                    payload={"step": 1, "text": "considering"},
                    occurred_at=datetime.now(UTC),
                ),
            )
            notification = websocket.receive_json()
            assert notification["method"] == "runtime.update"
            assert notification["params"]["session_id"] == "a"
            assert notification["params"]["type"] == (
                "assistant.thinking_delta"
            )
            assert notification["params"]["payload"]["text"] == (
                "considering"
            )
            assert notification["params"]["sequence"] is None


def test_gateway通过既有runtime_update转发process状态():
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
                    session_id="a",
                    task_id=None,
                    type="process.updated",
                    payload={
                        "process_id": "proc_1",
                        "pid": 123,
                        "status": "running",
                    },
                    occurred_at=datetime.now(UTC),
                ),
            )
            notification = websocket.receive_json()
            assert notification["method"] == "runtime.update"
            assert notification["params"]["type"] == "process.updated"
            assert notification["params"]["task_id"] is None
            assert notification["params"]["payload"]["status"] == "running"


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


def test_v2_service_methods_are_authenticated_and_isolated_from_ordinary_rpc():
    class SemanticRuntime(RuntimeStub):
        notifications = 0
        async def scoped_sessions(self, scope, *, cursor=None, limit=10):
            return {'items': [], 'next_cursor': None}
        async def scoped_notify(self, *, wake_id, session_id):
            self.notifications += 1
            return {'accepted': True, 'merged': self.notifications > 1}
        async def submit(self, *args, **kwargs):
            raise AssertionError('notify must not submit')
        async def steer_task(self, *args, **kwargs):
            raise AssertionError('notify must not steer')
        async def cancel_task(self, *args, **kwargs):
            raise AssertionError('notify or disconnect must not cancel')
    from apps.gateway.test.test_argus_methods import service_params, v2_fixture
    runtime = SemanticRuntime()
    with TestClient(create_app(runtime, argus_token_loader=lambda: 'fixture-secret')) as client:
        with client.websocket_connect('/rpc') as websocket:
            for method in ('argus.sessions', 'argus.notify'):
                websocket.send_json(request(method, service_params()))
                assert websocket.receive_json()['error']['code'] == -32601
        with client.websocket_connect('/argus/rpc') as websocket:
            for method in ('session.submit', 'session.steer', 'session.cancel', 'unknown.operation'):
                websocket.send_json(request(method, service_params()))
                assert websocket.receive_json()['error']['code'] == -32601
            for method in ('argus.sessions', 'argus.notify'):
                websocket.send_json(request(method, {'protocol_version': 2}))
                assert websocket.receive_json()['error']['data'] == {'code': 'unauthorized', 'retryable': False}
            websocket.send_json(request('argus.sessions', service_params(scope=v2_fixture()['request']['scope'])))
            assert websocket.receive_json()['result'] == {'protocol_version': 2, 'sessions': [], 'next_cursor': None}
            websocket.send_json(request('argus.notify', service_params(**v2_fixture()['notify'])))
            assert websocket.receive_json()['result'] == {'protocol_version': 2, 'accepted': True, 'merged': False}
        with client.websocket_connect('/argus/rpc') as websocket:
            websocket.send_json(request('argus.notify', service_params(**v2_fixture()['notify'])))
            assert websocket.receive_json()['result'] == {'protocol_version': 2, 'accepted': True, 'merged': True}
        assert runtime.notifications == 2
