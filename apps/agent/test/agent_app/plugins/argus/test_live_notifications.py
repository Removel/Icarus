import asyncio
import pytest
from types import SimpleNamespace

from agent_app.plugins.argus.plugin import ArgusPlugin
from agent_app.contracts.prompt import PromptUpdateEvent
from icarus.core.contracts.input import UserInputEvent,InputFinishedEvent
from icarus.core.contracts.context import BlackboardContextReadyEvent
from icarus.core.run_control import TaskChannelRegistry


def test_task_notice_is_coalesced_has_no_body_and_stops_on_finish(monkeypatch):
    assert hasattr(ArgusPlugin,'notify'),'running availability notifier is missing'
    import httpx
    calls=[];state={'pending_count':1,'watermark':'one'}
    async def respond(request):calls.append(request);return httpx.Response(200,json=state)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    async def run():
        binding={'view_version':3,'observation_prompt':'BODY','finished':False,
            'wake':{'binding_id':'binding','argus_url':'http://argus'}}
        bridge=SimpleNamespace(bindings={'task':binding},listeners={})
        channels=TaskChannelRegistry();channel=channels.create('task');channel.mark_preparing_context()
        plugin=ArgusPlugin('argus',bridge,channels);events=[];noticed=asyncio.Event()
        async def publish(event):
            events.append(event)
            if isinstance(event,PromptUpdateEvent):noticed.set()
        plugin.bind_publisher(publish)
        plugin.bind_background_work_starter(lambda name,operation:asyncio.create_task(operation(),name=name))
        await plugin.consume('user-input',UserInputEvent(task_id='task',prompt='subject'))
        for _ in range(10):plugin.notify('task','one')
        assert not calls,'availability must wait for finalized context'
        channel.start_run('run')
        await plugin.consume('blackboard',BlackboardContextReadyEvent(task_id='task',model_role='thinking',system_prompt='',input_prompt='BODY'))
        await asyncio.wait_for(noticed.wait(),2)
        for _ in range(10):plugin.notify('task','one')
        await asyncio.sleep(0)
        notices=[e for e in events if isinstance(e,PromptUpdateEvent)]
        assert len(notices)==1
        assert 'argus_receive' in notices[0].sections[0].content
        assert 'BODY' not in notices[0].sections[0].content and 'binding' not in notices[0].sections[0].content
        await plugin.consume('user-input',InputFinishedEvent(task_id='task',status='completed'))
        assert not plugin._watchers
        assert 'task' not in bridge.listeners
        assert not plugin.notify('task','two')
        await plugin.stop()


@pytest.mark.parametrize('ended',['commit','channel'])
def test_inflight_available_cannot_publish_after_task_ends(monkeypatch,ended):
    from agent_app.plugins.argus.notifications import watch
    from agent_app.plugins.argus import notifications
    entered=asyncio.Event();release=asyncio.Event()
    async def available(client,binding):
        entered.set();await release.wait()
        return {'pending_count':1,'watermark':'new'}
    monkeypatch.setattr(notifications,'available',available)
    async def run():
        channels=TaskChannelRegistry();channel=channels.create('task');channel.mark_preparing_context();channel.start_run('run')
        binding={'finished':False};bridge=SimpleNamespace(bindings={'task':binding})
        plugin=ArgusPlugin('argus',bridge,channels);events=[]
        async def publish(event):events.append(event)
        plugin.bind_publisher(publish);plugin._signals['task']=asyncio.Event()
        worker=asyncio.create_task(watch(plugin,'task',binding,plugin._signals['task']))
        try:
            await asyncio.wait_for(entered.wait(),1)
            if ended=='commit':binding['finished']=True
            else:channel.mark_completed()
            assert not plugin.notify('task','new'),'terminal channels cannot accept notification'
            release.set()
            await asyncio.wait_for(worker,1)
            assert not events,'no availability notice may be published after terminal transition'
        finally:
            release.set();worker.cancel();await asyncio.gather(worker,return_exceptions=True)
    asyncio.run(run())
