"""The service WebSocket has no ordinary session execution entrypoint."""

from fastapi.testclient import TestClient

from apps.gateway.test.test_app import RuntimeStub


def test_argus_service_path_rejects_ordinary_rpc_and_requires_token():
    from apps.gateway.src.app import create_app
    runtime = RuntimeStub()
    app = create_app(runtime, argus_token_loader=lambda: 'fixture-secret')
    with TestClient(app) as client:
        with client.websocket_connect('/argus/rpc') as websocket:
            websocket.send_json({'jsonrpc': '2.0', 'id': '1', 'method': 'session.create',
                                 'params': {'workspace_path': '/tmp/injected'}})
            assert websocket.receive_json()['error']['code'] == -32601
            websocket.send_json({'jsonrpc': '2.0', 'id': '2', 'method': 'argus.status',
                                 'params': {'protocol_version': 1, 'wake_id': '10000000-0000-4000-8000-000000000001'}})
            assert websocket.receive_json()['error']['data']['code'] == 'unauthorized'
        with client.websocket_connect('/rpc') as websocket:
            websocket.send_json({'jsonrpc': '2.0', 'id': '3', 'method': 'argus.status', 'params': {}})
            assert websocket.receive_json()['error']['code'] == -32601


def test_service_shell_shares_runtime_and_has_no_ordinary_rpc():
    from apps.gateway.src.app import create_app
    runtime = RuntimeStub()
    owner = create_app(runtime, argus_token_loader=lambda: 'fixture-secret')
    service = create_app(shared_app=owner, service_only=True, argus_token_loader=lambda: 'fixture-secret')
    with TestClient(owner):
        with TestClient(service) as client:
            assert runtime.started == 1
            assert service.state.agent_runtime is owner.state.agent_runtime
            with client.websocket_connect('/argus/rpc') as websocket:
                websocket.send_json({'jsonrpc': '2.0', 'id': 1, 'method': 'session.create', 'params': {}})
                assert websocket.receive_json()['error']['code'] == -32601
            assert client.get('/rpc').status_code == 404
        assert runtime.stopped == 0


def test_service_rejects_oversize_frame_before_json_parsing():
    from apps.gateway.src.app import create_app
    with TestClient(create_app(RuntimeStub())) as client:
        with client.websocket_connect('/argus/rpc') as websocket:
            websocket.send_text('x' * (1048576 + 1))
            result = websocket.receive_json()
            assert result['error']['code'] == -32600
