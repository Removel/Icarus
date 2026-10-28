"""Real upload/RPC/runtime/provider conversion, using an isolated local provider."""

import base64
import time

from fastapi.testclient import TestClient

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_resources import PNG
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, local_provider


def call(websocket, method, params):
    websocket.send_json({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    while True:
        response = websocket.receive_json()
        if response.get('id') == 1:
            assert 'error' not in response, response
            return response['result']


def test_upload_pure_image_to_model_and_read_history_after_runtime_restart(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    data_dir = tmp_path / 'data'
    params = {'workspace_path': str(workspace), 'session_id': 'image-test'}
    with local_provider() as port:
        config = config_for(data_dir, port)
        runtime = AgentRuntime(config_loader=lambda: config)
        with TestClient(create_app(runtime, resource_data_dir=data_dir)) as client:
            uploaded = client.post('/resources', content=PNG, headers={'Content-Type': 'image/png'})
            assert uploaded.status_code == 200
            with client.websocket_connect('/rpc') as websocket:
                call(websocket, 'session.create', params | {'load_runtime': False})
                accepted = call(websocket, 'session.submit', params | {
                    'prompt': '', 'resources': [uploaded.json()], 'submission_id': 'image-first',
                })
                for _ in range(200):
                    history = call(websocket, 'session.get_history', params)
                    finished = [row for row in history['records'] if row['type'] == 'task.finished']
                    if finished:
                        break
                    time.sleep(0.02)
                assert finished and finished[0]['payload']['status'] == 'completed', history
                users = [row for row in history['records'] if row['type'] == 'user.message']
                assert len(users) == 1 and users[0]['payload']['text'] == ''
                image = users[0]['payload']['resources'][0]
                assert image['resource_id'].startswith('assets/')
                assert client.get('/resources', params=params | image).content == PNG
                # Unknown-result retry keeps exactly the same task and no duplicate user record.
                again = call(websocket, 'session.submit', params | {
                    'prompt': '', 'resources': [uploaded.json()], 'submission_id': 'image-first',
                })
                assert again['task_id'] == accepted['task_id']
        restored = AgentRuntime(config_loader=lambda: config)
        with TestClient(create_app(restored, resource_data_dir=data_dir)) as client:
            with client.websocket_connect('/rpc') as websocket:
                history = call(websocket, 'session.get_history', params)
                users = [row for row in history['records'] if row['type'] == 'user.message']
                assert len(users) == 1 and users[0]['payload']['resources'] == [image]
            response = client.get('/resources', params=params | image)
            assert response.status_code == 200 and response.content == PNG
    requests = [payload for path, payload in ProviderHandler.requests if path.endswith('/chat/completions')]
    assert len(requests) == 1
    parts = [part for message in requests[0]['messages'] if message['role'] == 'user'
             for part in message['content'] if isinstance(part, dict)]
    url = next(part['image_url']['url'] for part in parts if part.get('type') == 'image_url')
    assert url.startswith('data:image/png;base64,')
    assert base64.b64decode(url.split(',', 1)[1]) == PNG
