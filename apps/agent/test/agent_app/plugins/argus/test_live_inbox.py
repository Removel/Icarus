import asyncio
import json

import pytest

from agent_app.plugins.argus.tools import ArgusTool,TOOL_NAMES
from icarus.core.models.messages import Message
from icarus.core.tools import ToolExecutionResult
from icarus.core.tools.result_budget import serialize_tool_result
from .test_commit_tools import fixture


def test_complete_tool_view_not_preview_is_delivery_proof():
    from agent_app.plugins.argus.inbox import visible_output
    output={'messages':[{'ref':'m2','text':'full supplement'}]}
    assert visible_output(output,[Message('tool',serialize_tool_result(ToolExecutionResult(True,output=output)))])
    assert not visible_output(output,[Message('tool',serialize_tool_result(ToolExecutionResult(True,output={'messages':[]})))])
    assert not visible_output(output,[Message('assistant',serialize_tool_result(ToolExecutionResult(True,output=output)))])


def test_receive_restores_lost_claim_receipt_and_acknowledges_only_final_view(monkeypatch):
    assert 'argus_receive' in TOOL_NAMES,'running read/wait tool is missing'
    import httpx
    from types import SimpleNamespace
    from agent_app.plugins.argus.references import References
    original=httpx.AsyncClient
    plugin,binding,records,requests=fixture(monkeypatch)
    binding.update(view_version=3,observation_prompt='BODY',message_refs={'observation':'m1'},source_refs={'qq:1':'s1'},delivered=True)
    binding['wake']['channel_snapshot']={'source_keys':['qq:1']}
    original_append=plugin.bridge.append
    async def append(identity,kind,payload,task_id=None):
        return await original_append(identity,kind,payload,task_id)
    plugin.bridge.append=append
    plugin.bridge.references=References(plugin.bridge)
    records.append(SimpleNamespace(type='argus.references',task_id=None,payload={'messages':{'observation':'m1'},'sources':{'qq:1':'s1'}}))
    posts=[];lost=[False]
    async def respond(request):
        if request.method=='GET':return httpx.Response(200,json={'pending_count':1,'watermark':'watermark'})
        body=json.loads(request.content);posts.append(body)
        if body['kind']=='agent.batch_requested':
            if not lost[0]:
                lost[0]=True;raise httpx.ReadError('lost claim response')
            return httpx.Response(200,json={'applied':True,'batch_id':body['request_id'],
                'items':[{'id':'extra','source_key':'qq:1','message_id':'platform','content':{'text':'supplement'},'detail':{}}]})
        if body['kind']=='agent.delivered':return httpx.Response(200,json={'applied':True})
        assert {x['observation_id'] for x in body['payload']['results']}=={'observation','extra'}
        return httpx.Response(200,json={'applied':True,'committed':True})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    async def run():
        receive=ArgusTool(plugin,'argus_receive')
        assert not (await receive.ainvoke({},task_id='task')).success
        result=await receive.ainvoke({},task_id='task');assert result.success,result.error
        assert result.output['work_refs']==['m1','m2']
        claim_ids=[p['request_id'] for p in posts if p['kind']=='agent.batch_requested']
        assert len(claim_ids)==2 and len(set(claim_ids))==1
        repeated=await receive.ainvoke({},task_id='task');assert repeated.output==result.output
        assert not any(p['kind']=='agent.delivered' for p in posts)
        args={'results':[{'ref':r,'status':'handled'} for r in ('m1','m2')]}
        assert not (await ArgusTool(plugin,'argus_commit').ainvoke(args,task_id='task')).success
        tool_message=Message('tool',serialize_tool_result(result))
        done=await ArgusTool(plugin,'argus_commit').ainvoke(args,task_id='task',task_messages=(tool_message,))
        assert done.success,done.error
        assert [p['payload']['observation_ids'] for p in posts if p['kind']=='agent.delivered']==[['extra']]
    asyncio.run(run())


def test_truncated_large_batch_can_progress_with_smaller_receive_pages():
    import httpx
    from types import SimpleNamespace
    from agent_app.plugins.argus.inbox import receive,synchronize
    from agent_app.plugins.argus.tools import MODELS
    from icarus.core.run_control import TaskChannelRegistry
    assert 'max_chars' in MODELS['argus_receive'].model_json_schema()['properties'],'no way to shrink an unpresentable batch'
    channels=TaskChannelRegistry();channels.create('task')
    extra={'id':'extra','source_key':'source','content':{'text':'正文'*700},'detail':{}}
    full={'messages':[{'ref':'m2','text':'正文'*700}],'work_refs':['m1','m2']}
    batch={'batch_id':'batch','items':[extra],'view':full,'delivered':False}
    binding={'wake':{'items':[{'id':'first'}]},'message_refs':{'first':'m1','extra':'m2'},'inbox_batches':[batch]}
    posts=[]
    class Tool:
        plugin=SimpleNamespace(task_channels=channels)
        async def _callback(self,client,task_id,binding,kind,payload,request_id):posts.append(payload);return {'applied':True}
    async def run():
        from icarus.core.tools.result_budget import render_tool_result
        async with httpx.AsyncClient() as client:
            original_result=ToolExecutionResult(True,output=full)
            truncated=render_tool_result(original_result,max_tokens=600,preview_tokens=400)
            assert truncated.metadata['result_truncated'] is True
            history=[Message('tool',serialize_tool_result(truncated))]
            result=await receive(Tool(),client,'task',binding,{'max_chars':256},history)
            assert not posts
            outputs=[]
            for _ in range(30):
                assert result.success
                outputs.append(result.output)
                rendered=render_tool_result(result,max_tokens=600,preview_tokens=400)
                assert not rendered.metadata['result_truncated']
                history.append(Message('tool',serialize_tool_result(rendered)))
                await synchronize(Tool(),client,'task',binding,history)
                if batch['delivered']:break
                result=await receive(Tool(),client,'task',binding,{'max_chars':256},history)
            assert batch['delivered'],'read recovery never reaches the full batch'
            assert len(posts)==1
            assert ''.join(o['observation_text'] for o in outputs)==json.dumps(full,ensure_ascii=False,sort_keys=True)
            assert all(o['text_offset']<o['next_text_offset'] for o in outputs)
    asyncio.run(run())


@pytest.mark.parametrize('args',[{'timeout_seconds':31},{'timeout_seconds':-1},{'limit':0},{'limit':21}])
def test_receive_bounds_are_explicit(args):
    from agent_app.plugins.argus.tools import MODELS
    with pytest.raises(ValueError):MODELS['argus_receive'].model_validate(args)


def test_empty_receive_wait_is_bounded_and_does_not_claim(monkeypatch):
    import httpx
    original=httpx.AsyncClient
    plugin,binding,records,requests=fixture(monkeypatch);calls=[]
    binding['message_refs']={'observation':'m1'}
    async def respond(request):
        calls.append(request.method)
        return httpx.Response(200,json={'pending_count':0,'watermark':None})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    async def run():
        binding['availability_signal']=asyncio.Event()
        result=await asyncio.wait_for(ArgusTool(plugin,'argus_receive').ainvoke({'timeout_seconds':.55},task_id='task'),2)
        assert result.success and result.output['messages']==[]
        assert 'POST' not in calls
        assert len(calls)<=2,'a bounded wait should sleep on notifications, not spam status reads'
        binding['finished']=True
        assert not (await ArgusTool(plugin,'argus_receive').ainvoke({},task_id='task')).success
    asyncio.run(run())
