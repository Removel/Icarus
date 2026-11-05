"""Real SessionHost discovers help only on demand; memory sees business text."""
import asyncio
import json
from uuid import uuid4

import httpx

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app
from apps.gateway.test.test_runtime_e2e import ProviderHandler, config_for, local_provider


class HelpProvider(ProviderHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append((self.path,payload))
        if self.path.endswith('/chat/completions'):
            count=sum(m['role']=='tool' for m in payload['messages'])
            steps=[('tool_search',{'query':'argus','name':'argus_guide'}),
                ('tool_execute',{'name':'argus_guide','arguments':{'topic':'rules'}}),
                ('tool_execute',{'name':'argus_commit','arguments':{'results':[{'ref':'m1','status':'no_action'}]}})]
            if count<len(steps):
                name,args=steps[count]
                delta={'tool_calls':[{'index':0,'id':f'call-{count}','type':'function',
                    'function':{'name':name,'arguments':json.dumps(args)}}]}
            else:delta={'content':'done'}
            chunks=[{'id':'fixture','object':'chat.completion.chunk','created':1,'model':'e2e',
                'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                {'id':'fixture','object':'chat.completion.chunk','created':1,'model':'e2e',
                'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if count<len(steps) else 'stop'}]}]
            data=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
            content_type='text/event-stream'
        else:
            result={'applied':True,'committed':payload.get('kind')=='agent.commit_requested'} if self.path=='/agent/events' else {'results':[]}
            data=json.dumps(result).encode();content_type='application/json'
        self.send_response(200);self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


def test_help_is_discovered_on_demand_and_recall_uses_business_subject(tmp_path,monkeypatch):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','synthetic')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','synthetic')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH',str(tmp_path/'workspace'))
    ids=[str(uuid4()) for _ in range(4)];channel,wake,binding,item=ids
    async def run(port):
        runtime=AgentRuntime(config_loader=lambda:config_for(tmp_path/'data',port));app=create_app(runtime)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://gateway',headers={'Authorization':'Bearer synthetic'}) as client:
                session=(await client.post('/argus/session',json={'channel_id':channel,'request_id':'select','session_key':'slot'})).json()['session_id']
                payload=dict(channel_id=channel,wake_id=wake,binding_id=binding,session_id=session,
                    session_key='slot',channel_version=1,rule_sdk={'version':2,'max_calls':3},
                    channel_snapshot=dict(id=channel,title='task',goal='review rule',progress='',
                        lifecycle='active',version=1,source_keys=['qq:100:group:42'],rule_source=''),
                    items=[dict(id=item,source_key='qq:100:group:42',message_id='platform-message',revision=1,
                        kind='message.observed',observed_at='2026-11-03T12:30:00Z',
                        content={'text':'Check whether this rule needs updating','sender_name':'Member'},
                        detail={'source':{'platform':'qq','kind':'group','region_id':'42','account_id':'100'}})],
                    argus_url=f'http://127.0.0.1:{port}')
                response=await client.post('/argus/wake',json=payload);assert response.status_code==200,response.text
                for _ in range(300):
                    status=(await client.post('/argus/status',json={'wake_id':wake})).json()
                    if status['status'] in {'completed','failed'}:break
                    await asyncio.sleep(.02)
                assert status['commit_state']=='applied',status
    with local_provider(HelpProvider) as port:asyncio.run(run(port))
    model=[p for path,p in HelpProvider.requests if path.endswith('/chat/completions')]
    assert len(model)==4
    first=json.dumps(model[0]['messages'],ensure_ascii=False)
    assert 'def main(event, channel)' not in first
    assert all(id not in first for id in ids)
    assert 'Check whether this rule needs updating' in first
    assert '42' in first and 'm1' in first
    assert 'def main(event, channel)' in str(model[2]['messages'])
    memory=[p for path,p in HelpProvider.requests if path=='/search']
    assert len(memory)==1
    assert memory[0]['query']=='Member: Check whether this rule needs updating'
    assert 'ARGUS' not in memory[0]['query']
