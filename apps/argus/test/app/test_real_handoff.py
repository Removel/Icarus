"""Three real applications over TCP/HTTP with isolated PG/SQLite and a local model."""

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
import uvicorn

from apps.argus.src.main import create_app
from apps.argus.src.core.config import ArgusConfig
from apps.argus.src.common.clients.gateway_agent_client import GatewayAgentClient, wake_wire
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.test.factories import draft_factory


class Model(BaseHTTPRequestHandler):
    requests = []
    observation_id = None
    incidental_id = None
    report_incidental = False
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if not self.path.endswith('/chat/completions'):
            raw, content_type = b'{"results": []}', 'application/json'
        else:
            type(self).requests.append(body)
            tool_results = [value for value in body['messages'] if value.get('role') == 'tool']
            completed_operations = len(tool_results)
            if completed_operations < 3:
                operation, arguments = (
                    ('argus_read', {'observation_id': self.observation_id}) if completed_operations == 0 else
                    ('argus_wait', {'timeout_seconds': 0}) if completed_operations == 1 else
                    ('argus_report', {'items': [{'observation_id': self.observation_id, 'status': 'handled'}] +
                        ([{'observation_id': self.incidental_id, 'status': 'handled'}] if self.report_incidental else [])}))
                delta = {'tool_calls': [{'index': 0, 'id': f'call-{completed_operations}', 'type': 'function', 'function': {
                    'name': operation, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'content': 'done'}, 'stop'
            chunks = [{'id': 'local', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'local',
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'local', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'local',
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}]
            raw = ''.join('data: '+json.dumps(value)+'\n\n' for value in chunks).encode()+b'data: [DONE]\n\n'
            content_type = 'text/event-stream'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def free_socket():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    return sock


@pytest.mark.parametrize('report_incidental', [False, True])
async def test_real_model_reports_initial_through_gateway_and_argus(database, tmp_path, monkeypatch, report_incidental):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'fixture-data')
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'fixture-control')
    app = create_app(ArgusConfig(execution={'mode': 'gateway'}), database=database)
    sock = free_socket()
    argus_port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    started = asyncio.Event()
    startup = server.startup
    async def start(*args, **kwargs):
        await startup(*args, **kwargs)
        started.set()
    server.startup = start
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    process = None
    Model.requests = []
    try:
        await asyncio.wait_for(started.wait(), 10)
        repo = app.state.repository
        draft = draft_factory('Please inspect this observation', message_id='88', locator={
            'account_id': '123', 'chat_kind': 'group', 'external_id': '456', 'message_id': '88', 'time': 10})
        initial, _ = await repo.upsert_revision(draft)
        Model.observation_id = str(initial.id)
        wake = await repo.commit_judgement(initial.chat_id, JudgementOutcome(
            selected=(SelectedItem(item=initial, excerpt='Please inspect this observation'),), decision='wake', reason='test'))
        incidental, _ = await repo.upsert_revision(draft_factory('Independent question B', message_id='89', locator={
            'account_id': '123', 'chat_kind': 'group', 'external_id': '456', 'message_id': '89', 'time': 11}))
        Model.incidental_id, Model.report_incidental = str(incidental.id), report_incidental
        model_port = provider.server_address[1]
        model = {'model_name': 'local', 'context_window': 128000, 'max_tokens': 200, 'temperature': 0, 'default_think_level': 'low'}
        endpoint = f'http://127.0.0.1:{model_port}'
        config = {'openai_base_url': endpoint+'/v1', 'anthropic_base_url': endpoint, 'openai_api_key': 'fixture',
            'icarus_data_dir': str(tmp_path/'agent-data'), 'model_settings': {'thinking': model, 'perception': model},
            'argus': {'enabled': True, 'workspace': str(tmp_path/'background'), 'base_url': f'http://127.0.0.1:{argus_port}'},
            'runtime': {'plugin_config': {'memory': {'user_id': 'fixture', 'agent_id': 'fixture', 'endpoint': endpoint},
                'knowledge': {'knowledge_base': 'fixture', 'endpoint': endpoint}}}}
        config_path = tmp_path/'agent.json'
        (tmp_path/'background').mkdir()
        config_path.write_text(json.dumps(config))
        gateway_socket = free_socket()
        gateway_port = gateway_socket.getsockname()[1]
        gateway_socket.close()
        root = Path(__file__).resolve().parents[4]
        log = (tmp_path/'gateway.log').open('w')
        process = subprocess.Popen([str(root/'apps/gateway/.venv/bin/python'), '-m', 'apps.gateway.test.handoff_host',
            str(config_path), str(gateway_port)], cwd=root, env={**os.environ}, stdout=log, stderr=subprocess.STDOUT)
        async with httpx.AsyncClient() as http:
            async with asyncio.timeout(15):
                while True:
                    if process.poll() is not None:
                        raise AssertionError((tmp_path/'gateway.log').read_text())
                    try:
                        if (await http.get(f'http://127.0.0.1:{gateway_port}/health')).status_code == 200: break
                    except httpx.TransportError: pass
                    await asyncio.sleep(0.05)
        client = GatewayAgentClient(f'ws://127.0.0.1:{gateway_port}/argus/rpc', 'fixture-control', timeout_seconds=10)
        try:
            assert (await client.submit(wake.payload)).accepted
            async with asyncio.timeout(15):
                while True:
                    view = await client.status(wake_id=wake.id)
                    if view['release_complete']: break
                    await asyncio.sleep(0.05)
            assert view['report_cursor'] == 1
            assert (await repo.get_item(initial.id)).status == 'processed'
            assert (await repo.get_wake(wake.id)).handoff['finalize']['initial_results'][0]['result'] == 'handled'
            assert len(Model.requests) == 4
            saved_b = await repo.get_item(incidental.id)
            assert saved_b.exposed_wake_id == wake.id
            assert saved_b.status == ('processed' if report_incidental else 'new')
            assert saved_b.wake_id == (wake.id if report_incidental else None)
        finally:
            await client.aclose()
    finally:
        if process is not None:
            process.terminate()
            await asyncio.to_thread(process.wait, 10)
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        sock.close()
        await asyncio.to_thread(provider.shutdown)
        provider.server_close()
        thread.join(timeout=5)
