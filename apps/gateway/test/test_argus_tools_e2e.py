"""Full offline provider -> Agent -> Argus HTTP callback -> receipt cycle."""
import asyncio
import json
import re
from uuid import uuid4
import httpx
from icarus.core.identity import SessionIdentity
from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, local_provider


class ArgusProvider(ProviderHandler):
    callbacks = []
    model_calls = []

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/agent/events':
            assert self.headers['Authorization'] == 'Bearer callback-secret'
            type(self).callbacks.append(payload)
            data, content_type = json.dumps({'applied':True, 'committed': payload['kind'] == 'agent.commit_requested'}).encode(), 'application/json'
        elif self.path.endswith('/chat/completions'):
            type(self).model_calls.append(payload)
            messages = payload['messages']
            start = max((i for i, m in enumerate(messages) if m['role'] == 'user'
                         and 'ARGUS CURRENT MESSAGES' in str(m.get('content', ''))), default=0)
            tool_results = [m for m in messages[start:] if m['role'] == 'tool']
            count = len(tool_results)
            lazy = any(tool['function']['name'] == 'tool_search' for tool in payload.get('tools', []))
            operation = count - int(lazy)
            if lazy and count == 0:
                name, arguments = 'tool_search', {'query':'argus'}
            elif operation == 0:
                match = re.search(r'"ref":\s*"(m[0-9]+)"', str(messages[start].get('content','')))
                ref = match.group(1) if match else 'm1'
                name, arguments = 'argus_commit', {'results':[{'ref':ref,'status':'handled'}]}
            else:
                name, arguments = None, None
            if lazy and name == 'argus_commit':
                name, arguments = 'tool_execute', {'name':name,'arguments':arguments}
            delta = {'content':'round complete'} if name is None else {'tool_calls':[{
                'index':0,'id':f'call-{count}','type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}
            chunks = [{'id':'fixture','object':'chat.completion.chunk','created':1,'model':'e2e',
                       'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                      {'id':'fixture','object':'chat.completion.chunk','created':1,'model':'e2e',
                       'choices':[{'index':0,'delta':{},'finish_reason':'stop' if name is None else 'tool_calls'}]}]
            data = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
            content_type = 'text/event-stream'
        else:
            data, content_type = b'{"results":[]}', 'application/json'
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_real_agent_argus_tools_receipts_and_final_view(tmp_path, monkeypatch):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH',str(tmp_path / 'workspace'))
    channel, wake, binding, item = [str(uuid4()) for _ in range(4)]
    ArgusProvider.callbacks, ArgusProvider.model_calls = [], []
    async def run(config, port):
        runtime = AgentRuntime(config_loader=lambda:config)
        app = create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway',
                headers={'Authorization':'Bearer gateway-secret'}) as client:
                session = (await client.post('/argus/session',json={'channel_id':channel,'request_id':'session','session_key':'slot-a'})).json()['session_id']
                payload = dict(channel_id=channel,wake_id=wake,binding_id=binding,channel_version=1,session_id=session,session_key='slot-a',
                    channel_snapshot=dict(id=channel,title='task',goal='goal',progress='',lifecycle='active',version=1,source_keys=['qq:same-source'],rule_source=''), rule_sdk={'version':2},
                    items=[dict(id=item,source_key='qq:same-source',message_id='m',kind='message.observed',revision=1,
                                observed_at='2026-11-01T00:00:00Z',content={'text':'untrusted external evidence'},detail={})],
                    argus_url=f'http://127.0.0.1:{port}')
                result = await client.post('/argus/wake',json=payload)
                assert result.status_code == 200, result.text
                for _ in range(300):
                    status = (await client.post('/argus/status',json={'wake_id':wake})).json()
                    if status['status'] in {'completed','failed'}:
                        break
                    await asyncio.sleep(.02)
                assert status['status'] == 'completed', status
                records,_ = await runtime.session_store.read_updates(SessionIdentity.create(tmp_path / 'workspace',session))
                assert len([r for r in records if r.type == 'argus.receipt']) == 2
                assert all(r.payload.get('success',True) for r in records if r.type == 'tool.completed'), [dict(r.payload) for r in records if r.type == 'tool.completed']
    with local_provider(ArgusProvider) as port:
        asyncio.run(run(config_for(tmp_path / 'data',port),port))
    assert [p['kind'] for p in ArgusProvider.callbacks] == ['agent.delivered','agent.commit_requested']
    assert ArgusProvider.callbacks[0]['payload'] == {'observation_ids':[item]}
    assert ArgusProvider.callbacks[1]['payload']['results'][0]['observation_id'] == item
    assert ArgusProvider.callbacks[1]['payload']['expected_version'] == 1
    first = ArgusProvider.model_calls[0]
    exposed = {t['function']['name'] for t in first.get('tools', [])}
    lazy = 'tool_search' in exposed
    assert len(ArgusProvider.model_calls) == (3 if lazy else 2)
    user_view = '\n'.join(str(m['content']) for m in first['messages'] if m['role'] == 'user')
    system_view = '\n'.join(str(m['content']) for m in first['messages'] if m['role'] == 'system')
    assert 'ARGUS CURRENT MESSAGES' in user_view
    assert 'untrusted external evidence' in user_view and 'm1' in user_view
    assert all(value not in user_view for value in (item, binding, channel, 'rule_sdk', 'def main(event, channel):'))
    assert 'argus_guide' in system_view and 'independent Icarus instance' in system_view
    assert 'untrusted external evidence' not in system_view
    assert 'def main(event, channel):' not in system_view
    assert {'tool_search', 'tool_execute', 'mcp_tool_list', 'mcp_tool_search', 'mcp_tool_execute'} <= exposed
    assert 'argus(6)' in next(t['function']['description'] for t in first['tools'] if t['function']['name'] == 'tool_search')
    assert 'memory_recall' in next(t['function']['description'] for t in first['tools'] if t['function']['name'] == 'tool_search')
