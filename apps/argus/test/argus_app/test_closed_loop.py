"""Real TCP Argus -> Gateway -> production Agent -> callback -> rule publication.

Only the remote model is a deterministic local HTTP fixture. All application
plugins, PostgreSQL transactions, subprocess rules, and HTTP callbacks are real.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import os
import re
import socket
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
import uvicorn

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app as gateway_app
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, local_provider
from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store

NEW_RULE = 'def main(event, channel):\n    return {"passed": False, "reason": "review already handled"}\n'
INITIAL_RULE = '''def main(event, channel):
    if "approved" not in event["content"]["text"]:
        return {"passed": False, "reason": "waiting for approval"}
    history = capability("history", {"limit": 20})
    waiting = any("review request" in row["content"]["text"] for row in history["observations"])
    if not waiting:
        return {"passed": False, "reason": "no earlier request"}
    answer = capability("semantic", {"text": event["content"]["text"], "criterion": "approval arrived"})
    return {"passed": answer["match"], "reason": "combined two sources"}
'''


class ClosedLoopProvider(ProviderHandler):
    model_calls = []

    def do_POST(self):
        if not self.path.endswith('/chat/completions'):
            return super().do_POST()
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).model_calls.append(payload)
        count = len([m for m in payload['messages'] if m['role'] == 'tool'])
        if count == 0:
            # Bypass the adapter's client guard once to prove the real server gate.
            view = json.dumps(payload['messages'], ensure_ascii=False)
            binding = re.search(r'\\"binding_id\\": \\"([^\\"]+)\\"', view)
            if binding is None:
                user_text = '\n'.join(str(m.get('content', '')) for m in payload['messages'] if m['role'] == 'user')
                binding = re.search(r'"binding_id": "([^"]+)"', user_text)
            assert binding is not None
            result = httpx.post(type(self).argus_url + '/agent/events',
                headers={'Authorization': 'Bearer callback-secret'}, timeout=10,
                json={'binding_id': binding.group(1), 'request_id': 'direct-early-update',
                      'kind': 'channel.update_requested',
                      'payload': {'expected_version': 1, 'progress': 'must not commit'}})
            assert result.status_code == 422  # legacy multi-step writes are not accepted
        calls = [
            ('tool_search', {'query': 'argus'}),
            ('argus_rule_check', {'source': NEW_RULE}),
            ('argus_commit', {'results': [{'ref': '1', 'status': 'handled'}],
                              'progress': 'review handled from QQ and Feishu', 'rule_source': NEW_RULE}),
        ]
        if count < len(calls):
            name, arguments = calls[count]
            if name != 'tool_search':
                name, arguments = 'tool_execute', {'name': name, 'arguments': arguments}
            delta = {'tool_calls': [{'index': 0, 'id': f'closed-{count}', 'type': 'function',
                                     'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'content': 'review processed and next rule published'}, 'stop'
        chunks = [{'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                   'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                  {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture',
                   'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
        raw = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def listener():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.listen(128)
    return sock


@asynccontextmanager
async def serve(app, sock):
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError('server exited before startup')
                await asyncio.sleep(.01)
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 20)
        sock.close()


@pytest.mark.asyncio
async def test_two_sources_real_tcp_agent_confirmation_and_rule_rewrite(tmp_path, monkeypatch, database_url):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH', str(tmp_path / 'workspace'))
    gateway_socket, argus_socket = listener(), listener()
    gateway_url = f'http://127.0.0.1:{gateway_socket.getsockname()[1]}'
    argus_url = f'http://127.0.0.1:{argus_socket.getsockname()[1]}'
    url = os.environ['ARGUS_TEST_DATABASE_URL']
    store = Store(url)
    semantic_calls = []
    async def semantic(args):
        if args.get('mode') == 'discovery':
            return {'match': False, 'discovery': {'action': 'ignore'}, 'reason': 'existing workflow evidence'}
        semantic_calls.append(args)
        return {'match': True, 'reason': 'fixture approval'}
    config = ArgusConfig(database_url=SecretStr(url), data_dir=tmp_path / 'argus',
                         agent_token=SecretStr('callback-secret'), gateway_token=SecretStr('gateway-secret'),
                         gateway_url=gateway_url, public_url=argus_url, qq_token=SecretStr('qq-secret'),
                         feishu_verification_token=SecretStr('feishu-secret'), scan_interval=.05)
    argus = create_app(config, store=store, semantic=semantic)
    ClosedLoopProvider.model_calls = []
    ClosedLoopProvider.argus_url = argus_url
    with local_provider(ClosedLoopProvider) as provider_port:
        runtime = AgentRuntime(config_loader=lambda: config_for(tmp_path / 'agent', provider_port))
        gateway = gateway_app(runtime)
        async with serve(gateway, gateway_socket), serve(argus, argus_socket):
            async with httpx.AsyncClient(base_url=argus_url, timeout=15) as client:
                group = uuid4().int % 100000000000
                chat = 'oc_' + uuid4().hex
                sources = [f'qq:123:group:{group}', f'feishu:tenant-test:chat:{chat}']
                auth = {'Authorization': 'Bearer callback-secret'}
                created = await client.post('/channels', headers=auth,
                    json={'title': 'cross-source review', 'goal': 'wait for approval',
                          'source_keys': sources, 'rule_source': INITIAL_RULE})
                assert created.status_code == 200, created.text
                channel_id = created.json()['id']
                qq = {'post_type': 'message', 'self_id': 123, 'message_type': 'group', 'group_id': group,
                      'user_id': 789, 'message_id': uuid4().hex, 'raw_message': 'review request: wait for approval'}
                raw = json.dumps(qq).encode()
                signature = 'sha1=' + hmac.new(b'qq-secret', raw, hashlib.sha1).hexdigest()
                assert (await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})).status_code == 200
                # Wait for durable ignore cursor; no deep Agent call for the first source.
                async with asyncio.timeout(10):
                    while True:
                        ch = (await client.get(f'/channels/{channel_id}', headers=auth)).json()
                        if ch['subscriptions'][sources[0]] is not None:
                            break
                        await asyncio.sleep(.02)
                assert ClosedLoopProvider.model_calls == []
                feishu = {'schema': '2.0', 'header': {'event_type': 'im.message.receive_v1', 'tenant_key': 'tenant-test',
                            'token': 'feishu-secret', 'event_id': uuid4().hex}, 'event': {'sender': {'sender_id': {'open_id': 'ou_fixture'}},
                            'message': {'chat_id': chat, 'message_id': uuid4().hex, 'message_type': 'text',
                                        'content': json.dumps({'text': 'approved: interface review'})}}}
                response = await client.post('/protocols/feishu', json=feishu)
                assert response.status_code == 200, response.text
                async with asyncio.timeout(35):
                    while True:
                        ch = (await client.get(f'/channels/{channel_id}', headers=auth)).json()
                        if ch['version'] == 2 and ch['runtime'].get('round') is None:
                            break
                        await asyncio.sleep(.05)
                assert ch['progress'] == 'review handled from QQ and Feishu'
                assert ch['rule_hash'] == hashlib.sha256(NEW_RULE.encode()).hexdigest()
                assert ch['runtime']['pending'] == {}
                assert len(semantic_calls) == 1
                # finish receipt can arrive before the final model text is emitted.
                async with asyncio.timeout(10):
                    while len(ClosedLoopProvider.model_calls) < 4:
                        await asyncio.sleep(.02)
                assert len(ClosedLoopProvider.model_calls) == 4
                async with store.engine.connect() as conn:
                    names = set((await conn.execute(text('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'))).scalars())
                    assert names == {'observed_items', 'channels'}
                    controls = (await conn.execute(text("SELECT kind,content,detail FROM observed_items WHERE source_key LIKE 'agent:%' ORDER BY position"))).mappings().all()
                own = [r for r in controls if r['detail'].get('result', {}).get('channel_id') == channel_id or r['content'].get('progress') == 'must not commit']
                assert any(r['kind'] == 'agent.commit_requested' and r['detail']['result'].get('committed') for r in own)
                assert not any(r['kind'] in {'agent.result_submitted', 'channel.update_requested', 'agent.finish_requested'} for r in own)
                # Published rule suppresses the next event without another Agent execution.
                previous_cursor = ch['subscriptions'][sources[0]]
                raw = json.dumps(qq | {'message_id': uuid4().hex, 'raw_message': 'approved again'}).encode()
                signature = 'sha1=' + hmac.new(b'qq-secret', raw, hashlib.sha1).hexdigest()
                assert (await client.post('/protocols/qq', content=raw, headers={'X-Signature': signature})).status_code == 200
                async with asyncio.timeout(10):
                    while True:
                        final = (await client.get(f'/channels/{channel_id}', headers=auth)).json()
                        if final['subscriptions'][sources[0]] != previous_cursor:
                            break
                        await asyncio.sleep(.02)
                assert final['runtime']['pending'] == {}
                assert len(ClosedLoopProvider.model_calls) == 4
