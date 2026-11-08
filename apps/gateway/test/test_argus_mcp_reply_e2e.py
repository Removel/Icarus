"""Real MCP stdio + protected Gateway + normal Agent, never a real QQ send."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_argus_tools_e2e import ArgusProvider, current_argus_view
from apps.gateway.test.test_runtime_e2e import config_for, local_provider


class ReplyProvider(ArgusProvider):
    reply_retcode = 0
    lose_commit_response = False
    lost_once = False

    def do_GET(self):
        if self.path.startswith('/agent/commits/'):
            data = json.dumps({'state': 'applied', 'result': {'applied': True, 'committed': True}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return super().do_GET()

    def do_POST(self):
        if self.path == '/agent/events' and self.lose_commit_response:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            type(self).callbacks.append(body)
            committed = body['kind'] == 'agent.commit_requested'
            if committed and not type(self).lost_once:
                import socket
                type(self).lost_once = True
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            data = json.dumps({'applied': True, 'committed': committed}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not self.path.endswith('/chat/completions'):
            return super().do_POST()
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).model_calls.append(body)
        count = sum(m['role'] == 'tool' for m in body['messages'])
        view = current_argus_view(body['messages'])
        target = view['messages'][0]['reply_target']
        assert target['platform'] == 'qq' and target['kind'] == 'private'
        assert target['account_id'] == '100'
        sequence = [
            ('mcp_tool_search', {'server': 'snowluma', 'query': 'invoke_action'}),
            ('mcp_tool_execute', {'tool_ref': 'snowluma/invoke_action', 'arguments': {
                'action': 'send_private_msg', 'params': {'user_id': int(target['user_id']), 'message': 'synthetic reply'}}}),
            ('tool_execute', {'name': 'argus_commit', 'arguments': {'results': [
                {'ref': ref, 'status': 'handled' if self.reply_retcode == 0 else 'no_action',
                 'reason': 'platform success message_id=901' if self.reply_retcode == 0 else 'platform rejected send'} for ref in view['work_refs']],
                'progress': 'replied' if self.reply_retcode == 0 else 'send failed',
                'lifecycle': 'active', 'next_session': 'new'}}),
        ]
        if self.lose_commit_response:
            # Retry the exact commit after a missing receipt, never the MCP send.
            sequence.append(sequence[-1])
        if count < len(sequence):
            name, arguments = sequence[count]
            delta = {'tool_calls': [{'index': 0, 'id': f'call-{count}', 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'content': 'reply sent and committed'}, 'stop'
        chunks = [{'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic',
                   'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                  {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic',
                   'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
        data = (''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.mark.parametrize('retcode,lose_receipt', [(0, False), (1401, False), (0, True)])
@pytest.mark.parametrize('complete_window',[False,True])
def test_agent_sends_via_real_mcp_then_confirms_argus(tmp_path, monkeypatch, retcode, lose_receipt, complete_window):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH', str(tmp_path / 'workspace'))
    server = tmp_path / 'synthetic_mcp.py'
    sends = tmp_path / 'sends.jsonl'
    server.write_text('''import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer
server = MCPServer("synthetic-snowluma")
@server.tool()
def invoke_action(action: str, params: dict) -> dict:
    """Invoke a synthetic OneBot action; never calls any platform."""
    assert action == "send_private_msg"
    with Path(''' + repr(str(sends)) + ''').open("a") as f:
        f.write(json.dumps({"action": action, "params": params}) + "\\n")
    return {"status": "ok" if RETCODE == 0 else "failed", "retcode": RETCODE,
            "data": {"message_id": 901} if RETCODE == 0 else None}
if __name__ == "__main__":
    server.run(transport="stdio")
'''.replace('RETCODE', str(retcode)))
    channel, wake, binding, obs = [str(uuid4()) for _ in range(4)]
    ReplyProvider.callbacks, ReplyProvider.model_calls = [], []
    ReplyProvider.reply_retcode = retcode
    ReplyProvider.lose_commit_response = lose_receipt
    ReplyProvider.lost_once = False
    async def run(port):
        config = config_for(tmp_path / 'data', port)
        config.mcp_servers = {'snowluma': {'command': sys.executable, 'args': [str(server)]}}
        runtime = AgentRuntime(config_loader=lambda: config)
        app = create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway',
                headers={'Authorization': 'Bearer gateway-secret'}) as client:
                r = await client.post('/argus/session', json={'channel_id': channel, 'request_id': 'select', 'session_key': 'slot-a'})
                assert r.status_code == 200, r.text
                session = r.json()['session_id']
                payload = dict(channel_id=channel, wake_id=wake, binding_id=binding, channel_version=1,
                    session_id=session, session_key='slot-a', channel_snapshot=dict(id=channel, title='conversation',
                    goal='respond to greeting', progress='', lifecycle='active', version=1,
                    source_keys=['qq:100:private:200'], rule_source=''), rule_sdk={'version': 2},
                    items=[dict(id=obs, source_key='qq:100:private:200', message_id='m', kind='message.observed',
                        revision=1, observed_at='2026-11-03T00:00:00Z', content={'text': 'synthetic greeting', 'message_type': 'private'},
                        detail={'protocol':'onebot', 'self_id':'100', 'peer_id':'200'})],
                    argus_url=f'http://127.0.0.1:{port}')
                if complete_window:
                    body=[payload['items'][0]]
                    for fragment in ('有人来问项目','问有没有经费','嗯','我们是在一起做开源项目','我说的是项目协作'):
                        body.append(payload['items'][0] | {'id':str(uuid4()),'message_id':str(uuid4()),'content':{'text':fragment,'message_type':'private'}})
                    payload['window_items']=body
                    payload['history_items']=[payload['items'][0] | {'id':str(uuid4()),'message_id':'earlier',
                        'content':{'text':'earlier discussion'},'observed_at':'2026-11-02T00:00:00Z'}]
                response = await client.post('/argus/wake', json=payload)
                assert response.status_code == 200, response.text
                for _ in range(600):
                    status = (await client.post('/argus/status', json={'wake_id': wake})).json()
                    if status['status'] in {'completed', 'failed'}:
                        break
                    await asyncio.sleep(.02)
                assert status['status'] == 'completed', status
                assert status['commit_state'] == 'applied', status
                assert (await client.post('/argus/wake', json=payload)).json()['task_id'] == response.json()['task_id']
    with local_provider(ReplyProvider) as port:
        asyncio.run(run(port))
    assert [json.loads(line) for line in sends.read_text().splitlines()] == [
        {'action': 'send_private_msg', 'params': {'user_id': 200, 'message': 'synthetic reply'}}]
    commits = [x for x in ReplyProvider.callbacks if x['kind'] == 'agent.commit_requested']
    assert len(commits) == 1
    assert commits[0]['payload']['next_session'] == 'new'
    assert commits[0]['payload']['results'][0]['observation_id'] == obs
    assert commits[0]['payload']['results'][0]['status'] == ('handled' if retcode == 0 else 'no_action')
    assert commits[0]['payload']['progress'] == ('replied' if retcode == 0 else 'send failed')
    first_view = current_argus_view(ReplyProvider.model_calls[0]['messages'])
    assert first_view['work_refs']==['m1']
    if complete_window:
        assert [m['text'] for m in first_view['messages']]==[
            'synthetic greeting','有人来问项目','问有没有经费','嗯','我们是在一起做开源项目','我说的是项目协作']
        assert first_view['history'][0]['text']=='earlier discussion'
        assert first_view['history'][0]['ref'] not in first_view['work_refs']
    user_content='\n'.join(m['content'] if isinstance(m['content'],str) else '\n'.join(
        p.get('text','') for p in m['content'] if isinstance(p,dict))
        for m in ReplyProvider.model_calls[0]['messages'] if m['role']=='user')
    assert '<plugin_context>' in user_content
    trigger=user_content.split('<user_request>',1)[1].split('</user_request>',1)[0]
    assert 'ARGUS CURRENT MESSAGES' not in trigger
    assert 'Argus plugin' in trigger
    system_view = '\n'.join(str(m['content']) for m in ReplyProvider.model_calls[0]['messages'] if m['role'] == 'system')
    assert 'your platform identity' in system_view and 'before argus_commit' in system_view
    assert 'ordinary chat' in system_view and 'internal operator' in system_view
    assert 'qq:100:private:200' not in system_view and obs not in system_view
    # The scripted model saw the actual business envelope, not just MCP transport success.
    marker = '901' if retcode == 0 else '1401'
    assert any(marker in str(m) and 'retcode' in str(m) for p in ReplyProvider.model_calls for m in p['messages'] if m['role'] == 'tool')
