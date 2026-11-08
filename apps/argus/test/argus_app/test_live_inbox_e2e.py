"""Later accepted source window reaches one already-running Agent through notice/read."""
import asyncio
import hashlib
import hmac
import json
import sys
import threading

import httpx
import pytest
from pydantic import SecretStr

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app as gateway_app
from apps.gateway.test.test_argus_tools_e2e import ArgusProvider,current_argus_view
from apps.gateway.test.test_runtime_e2e import config_for,local_provider
from apps.argus.test.argus_app.test_closed_loop import listener,serve
from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store


class InboxProvider(ArgusProvider):
    first=threading.Event();release=threading.Event()
    def do_POST(self):
        if not self.path.endswith('/chat/completions'):return super().do_POST()
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).model_calls.append(body)
        tools=[m for m in body['messages'] if m['role']=='tool'];count=len(tools)
        if count==0:
            self.first.set()
            assert self.release.wait(15),'test did not deliver supplement to active task'
            name,args='tool_search',{'query':'argus','name':'argus_receive'}
        elif count==1:
            name,args='tool_execute',{'name':'argus_receive','arguments':{'timeout_seconds':1}}
        elif count==2:
            result=json.loads(tools[-1]['content'])['output']
            assert any(m['text']=='这是项目协作，不是借钱' for m in result['messages'])
            name,args='mcp_tool_search',{'server':'snowluma','query':'invoke_action'}
        elif count==3:
            name,args='mcp_tool_execute',{'tool_ref':'snowluma/invoke_action','arguments':{
                'action':'send_private_msg','params':{'user_id':200,'message':'结合补充后的回复'}}}
        elif count==4:
            received=next(json.loads(m['content'])['output'] for m in tools if '这是项目协作，不是借钱' in m['content'])
            name,args='tool_execute',{'name':'argus_commit','arguments':{
                'results':[{'ref':ref,'status':'handled'} for ref in received['work_refs']]}}
        else:name,args=None,None
        delta={'content':'done'} if name is None else {'tool_calls':[{'index':0,'id':'live-'+str(count),'type':'function',
            'function':{'name':name,'arguments':json.dumps(args)}}]}
        chunks=[{'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test',
            'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
            {'id':'fixture','object':'chat.completion.chunk','created':1,'model':'test',
            'choices':[{'index':0,'delta':{},'finish_reason':'stop' if name is None else 'tool_calls'}]}]
        raw=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)


@pytest.mark.asyncio
async def test_running_task_receives_supplement_without_second_task_or_send(tmp_path,monkeypatch,database_url):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','gateway-secret');monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH',str(tmp_path/'workspace'))
    server=tmp_path/'synthetic_mcp_server.py';sent=tmp_path/'sent.jsonl'
    server.write_text('''import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer
s=MCPServer("local")
@s.tool()
def invoke_action(action: str, params: dict) -> dict:
    """Synthetic QQ send, no actual platform call."""
    with Path('''+repr(str(sent))+''').open("a") as f:f.write(json.dumps(params)+"\\n")
    return {"status":"ok","retcode":0,"data":{"message_id":901}}
s.run(transport="stdio")
''')
    gs,ars=listener(),listener();gurl=f'http://127.0.0.1:{gs.getsockname()[1]}';aurl=f'http://127.0.0.1:{ars.getsockname()[1]}'
    store=Store(database_url)
    async def semantic(args):return {'match':True,'reason':'same matter'}
    config=ArgusConfig(database_url=SecretStr(database_url),data_dir=tmp_path/'argus',agent_token=SecretStr('callback-secret'),
        gateway_token=SecretStr('gateway-secret'),gateway_url=gurl,public_url=aurl,qq_token=SecretStr('qq-secret'),
        discovery_enabled=False,channel_naming_enabled=False,message_quiet_seconds=.05,scan_interval=.05)
    argus=create_app(config,store=store,semantic=semantic)
    InboxProvider.model_calls=[];InboxProvider.callbacks=[];InboxProvider.first=threading.Event();InboxProvider.release=threading.Event()
    with local_provider(InboxProvider) as port:
        settings=config_for(tmp_path/'agent',port);settings.mcp_servers={'snowluma':{'command':sys.executable,'args':[str(server)]}}
        runtime=AgentRuntime(config_loader=lambda:settings);gateway=gateway_app(runtime)
        async with serve(gateway,gs),serve(argus,ars):
            async with httpx.AsyncClient(base_url=aurl,timeout=10) as client:
                auth={'Authorization':'Bearer callback-secret'}
                response=await client.post('/channels',headers=auth,json={'title':'project','goal':'understand collaboration',
                    'source_keys':['qq:100:private:200'],'rule_source':'def main(event, channel):\n    return {"passed":True,"reason":"related"}\n'})
                assert response.status_code==200;cid=response.json()['id']
                async def send(mid,text):
                    raw=json.dumps({'post_type':'message','self_id':100,'user_id':200,'message_type':'private','message_id':mid,'raw_message':text}).encode()
                    r=await client.post('/protocols/qq',content=raw,headers={'X-Signature':'sha1='+hmac.new(b'qq-secret',raw,hashlib.sha1).hexdigest()})
                    assert r.status_code==200,r.text
                try:
                    await send('first','有人问有没有资金')
                    assert await asyncio.to_thread(InboxProvider.first.wait,10)
                    before=(await store.channel(cid)).runtime['round'];task=before['task_id']
                    await send('second','这是项目协作，不是借钱')
                    async with asyncio.timeout(10):
                        while True:
                            ch=await store.channel(cid)
                            state=await store.inbox_state(ch.runtime['round']['binding_id'])
                            if state['pending_count']:break
                            await asyncio.sleep(.02)
                    # Let the task receive the no-body availability notification before its next tool step.
                    await asyncio.sleep(.1)
                    InboxProvider.release.set()
                    async with asyncio.timeout(20):
                        while True:
                            ch=await store.channel(cid)
                            if not ch.runtime.get('round') and not ch.runtime['pending']:break
                            await asyncio.sleep(.03)
                    assert len(sent.read_text().splitlines())==1
                    from icarus.core.identity import SessionIdentity
                    session_ids=await runtime.session_store.list_session_ids(SessionIdentity.create(tmp_path/'workspace').workspace_key)
                    records,_=await runtime.session_store.read_updates(SessionIdentity.create(tmp_path/'workspace',session_ids[0]))
                    assert len([r for r in records if r.type=='argus.wake'])==1
                    assert len({r.task_id for r in records if r.type=='task.started'})==1
                    first=current_argus_view(InboxProvider.model_calls[0]['messages'])
                    assert [m['text'] for m in first['messages']]==['有人问有没有资金']
                    assert any('新的待读补充' in str(p['messages']) for p in InboxProvider.model_calls[1:])
                    assert len([r for r in records if r.type=='argus.callback' and r.payload['body']['kind']=='agent.commit_requested'])==1
                finally:InboxProvider.release.set()
