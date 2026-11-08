"""Persisted QQ window -> real Agent plugin context -> local MCP -> atomic PG commit."""
import asyncio
import hashlib
import hmac
import json
import sys

import httpx
import pytest
from pydantic import SecretStr

from agent_app.application import AgentRuntime
from apps.gateway.src.app import create_app as gateway_app
from apps.gateway.test.test_argus_mcp_reply_e2e import ReplyProvider
from apps.gateway.test.test_argus_tools_e2e import current_argus_view
from apps.gateway.test.test_runtime_e2e import config_for, local_provider
from apps.argus.test.argus_app.test_closed_loop import listener, serve
from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store


@pytest.mark.asyncio
async def test_full_window_body_reaches_first_model_and_sends_once(tmp_path,monkeypatch,database_url):
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN','gateway-secret')
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','callback-secret')
    monkeypatch.setenv('ARGUS_WORKSPACE_PATH',str(tmp_path/'workspace'))
    texts=['真有意思','有人来问项目','问有没有经费','嗯','我们是在一起做开源项目','我说的是项目协作']
    server=tmp_path/'mcp_fixture.py';sends=tmp_path/'sends.jsonl'
    server.write_text('''import json
from pathlib import Path
from mcp.server.mcpserver import MCPServer
server=MCPServer("local-qq-fixture")
@server.tool()
def invoke_action(action: str, params: dict) -> dict:
    """Run a synthetic private-message send, never connects to a real platform."""
    assert action=="send_private_msg"
    with Path('''+repr(str(sends))+''').open("a") as f:f.write(json.dumps(params)+"\\n")
    return {"status":"ok","retcode":0,"data":{"message_id":901}}
if __name__=="__main__":server.run(transport="stdio")
''')
    gs,ars=listener(),listener()
    gateway_url=f'http://127.0.0.1:{gs.getsockname()[1]}'
    argus_url=f'http://127.0.0.1:{ars.getsockname()[1]}'
    store=Store(database_url);semantic_windows=[]
    async def semantic(args):
        semantic_windows.append([m['text'] for m in args['context']['current_window']])
        return {'match':True,'reason':'same conversation'}
    config=ArgusConfig(database_url=SecretStr(database_url),data_dir=tmp_path/'argus',
        agent_token=SecretStr('callback-secret'),gateway_token=SecretStr('gateway-secret'),
        gateway_url=gateway_url,public_url=argus_url,qq_token=SecretStr('qq-secret'),
        discovery_enabled=False,channel_naming_enabled=False,scan_interval=.05)
    app=create_app(config,store=store,semantic=semantic)
    ReplyProvider.callbacks=[];ReplyProvider.model_calls=[]
    ReplyProvider.reply_retcode=0;ReplyProvider.lose_commit_response=False
    with local_provider(ReplyProvider) as port:
        settings=config_for(tmp_path/'agent',port)
        settings.mcp_servers={'snowluma':{'command':sys.executable,'args':[str(server)]}}
        runtime=AgentRuntime(config_loader=lambda:settings);gateway=gateway_app(runtime)
        async with serve(gateway,gs),serve(app,ars):
            async with httpx.AsyncClient(base_url=argus_url,timeout=15) as client:
                auth={'Authorization':'Bearer callback-secret'}
                rule='''def main(event, channel):
    if event["content"].get("text")=="我们是在一起做开源项目":
        return {"passed":False,"reason":"not a separate request"}
    answer=capability("semantic",{"text":event["content"]["text"],"criterion":"is this related?"})
    return {"passed":answer["match"],"reason":"conversation"}
'''
                created=await client.post('/channels',headers=auth,json={'title':'project collaboration','goal':'understand the current conversation',
                    'source_keys':['qq:100:private:200'],'rule_source':rule})
                assert created.status_code==200,created.text
                channel_id=created.json()['id'];observed=[]
                for n,text in enumerate(texts):
                    raw=json.dumps({'post_type':'message','self_id':100,'message_type':'private',
                        'user_id':200,'message_id':str(n),'raw_message':text}).encode()
                    sig='sha1='+hmac.new(b'qq-secret',raw,hashlib.sha1).hexdigest()
                    response=await client.post('/protocols/qq',content=raw,headers={'X-Signature':sig})
                    assert response.status_code==200,response.text
                    observed.append(response.json()['observation_id'])
                assert not ReplyProvider.model_calls,'the source quiet window has not elapsed'
                async with asyncio.timeout(30):
                    while True:
                        ch=(await client.get('/channels/'+channel_id,headers=auth)).json()
                        if ReplyProvider.model_calls and not ch['runtime'].get('round') and not ch['runtime']['pending']:
                            break
                        await asyncio.sleep(.03)
                assert semantic_windows==[texts]*5
                first=current_argus_view(ReplyProvider.model_calls[0]['messages'])
                assert [m['text'] for m in first['messages']]==texts
                assert len(first['work_refs'])==5
                assert first['messages'][4]['ref'] not in first['work_refs']
                assert first['messages'][4]['role']=='current'
                assert [json.loads(s) for s in sends.read_text().splitlines()]==[{'user_id':200,'message':'synthetic reply'}]
                assert ch['progress']=='replied'
                assert len(await store.channels())==1
