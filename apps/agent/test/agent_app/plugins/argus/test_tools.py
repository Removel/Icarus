"""Argus tool boundary tests; actual model view gates delivered callbacks."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from agent_app.argus_protocol import WakeRequest, Observation
from agent_app.plugins.argus.tools import ArgusTool
from icarus.core.models.messages import Message, TextPart
from icarus.core.run_control import TaskChannelRegistry


def test_confirm_rejects_missing_final_view_and_stale_refs(monkeypatch):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN','test-only')
    task_id = 'task'
    channels = TaskChannelRegistry()
    channels.create(task_id)
    item_id = str(uuid4())
    binding = {'wake':{'binding_id':'binding','channel_id':'channel','argus_url':'http://127.0.0.1:1',
                      'items':[{'id':item_id}]},'prompt':'FULL FROZEN CONTENT','delivered':False,'confirmed':[],'finished':False}
    bridge = SimpleNamespace(bindings={task_id:binding}, callback_locks={})
    plugin = SimpleNamespace(bridge=bridge, task_channels=channels)
    tool = ArgusTool(plugin, 'argus_commit')
    async def run():
        result = await tool.ainvoke({'results':[{'ref':'old','status':'handled'}]}, task_id=task_id)
        assert not result.success
        assert 'outside the current frozen round' in result.error
        result = await tool.ainvoke({'results':[{'ref':'1','status':'handled'}]}, task_id=task_id)
        assert not result.success
        assert 'Full frozen content is not visible' in result.error
        channels.request_cancel(task_id, 'stop')
        from icarus.core.run_control.channel import AgentRunCancelled
        with pytest.raises(AgentRunCancelled):
            await tool.ainvoke({'results':[{'ref':'1','status':'handled'}]}, task_id=task_id)
    asyncio.run(run())


def test_binding_revoked_while_waiting_for_callback_lock(monkeypatch):
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'test-only')
    binding = {'wake':{'items':[{'id':'item'}]}, 'finished':False}
    class RevokingLock:
        async def __aenter__(self):
            binding['finished'] = True
        async def __aexit__(self, *args):
            pass
    channels = TaskChannelRegistry()
    channels.create('task')
    bridge = SimpleNamespace(bindings={'task':binding}, callback_locks={'task':RevokingLock()})
    tool = ArgusTool(SimpleNamespace(bridge=bridge, task_channels=channels), 'argus_channel_read')
    result = asyncio.run(tool.ainvoke({}, task_id='task'))
    assert not result.success
    assert 'already finished' in result.error


def test_callback_request_ids_are_scoped_to_task_binding():
    import httpx
    channels = TaskChannelRegistry()
    channels.create('old-task')
    channels.create('new-task')
    records, calls = [], []
    class Store:
        async def read_updates(self, identity):
            return tuple(records), len(records)
    async def append(identity, kind, payload, task_id):
        records.append(SimpleNamespace(type=kind, payload=payload, task_id=task_id))
    bridge = SimpleNamespace(runtime=SimpleNamespace(session_store=Store()), identity=lambda channel:channel, append=append)
    tool = ArgusTool(SimpleNamespace(bridge=bridge, task_channels=channels), 'argus_commit')
    def respond(request):
        import json
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={'applied':True, 'binding_id':body['binding_id']})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for task, binding_id in [('old-task','old-binding'), ('new-task','new-binding')]:
                binding = {'wake':{'channel_id':'same-channel','binding_id':binding_id,'argus_url':'http://argus'}}
                first = await tool._callback(client, task, binding, 'agent.result_submitted', {'items':[]}, 'same-id')
                retry = await tool._callback(client, task, binding, 'agent.result_submitted', {'items':[]}, 'same-id')
                assert first == retry == {'applied':True,'binding_id':binding_id}
                with pytest.raises(ValueError, match='fingerprint conflict'):
                    await tool._callback(client, task, binding, 'agent.result_submitted', {'items':['changed']}, 'same-id')
    asyncio.run(run())
    assert len(calls) == 2


def test_wake_boundary_is_strict():
    with pytest.raises(ValueError):
        WakeRequest.model_validate({'channel_id':'channel', 'channel_version':True})


def test_observation_rejects_non_utc_time_and_non_json_content():
    valid = dict(id=str(uuid4()), source_key='qq:1', message_id='m', kind='message.observed',
                 revision=1, observed_at='2026-11-01T00:00:00Z', content={}, detail={})
    for values in ({'observed_at':'not-a-timestamp'}, {'observed_at':'2026-11-01T01:00:00+01:00'},
                   {'content':{'number':float('nan')}}):
        with pytest.raises(ValueError):
            Observation.model_validate(valid | values)
