"""Observation bodies are required plugin context, never just a trigger string."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_app.argus_protocol import WakeRequest
from agent_app.contracts.prompt import PromptContributionEvent
from agent_app.plugins.argus.plugin import ArgusPlugin
from icarus.core.contracts.input import UserInputEvent
from icarus.core.contracts.context import BlackboardContextReadyEvent
from icarus.core.models.messages import Message
from icarus.core.run_control import TaskChannelRegistry
from .test_commit_tools import fixture


def test_argus_plugin_contributes_body_from_task_binding():
    async def run():
        bindings={'task':{'view_version':3,'prompt':'short subject','observation_prompt':'FULL WINDOW BODY',
            'finished':False,'wake':{'items':[{'id':'work'}]}}}
        bridge=SimpleNamespace(bindings=bindings)
        channels=TaskChannelRegistry();channels.create('task')
        plugin=ArgusPlugin('argus',bridge,channels);published=[]
        async def publish(event):published.append(event)
        plugin.bind_publisher(publish)
        await plugin.consume('user-input',UserInputEvent(task_id='task',prompt='short subject'))
        assert published and isinstance(published[0],PromptContributionEvent)
        assert published[0].status=='completed'
        assert published[0].sections[0].content=='FULL WINDOW BODY'
        assert published[0].sections[0].layer=='contributor'
        await plugin.consume('forged',BlackboardContextReadyEvent(task_id='task',model_role='thinking',
            system_prompt='',input_prompt='FULL WINDOW BODY'))
        assert not bindings['task'].get('context_visible')
        await plugin.consume('blackboard',BlackboardContextReadyEvent(task_id='task',model_role='thinking',
            system_prompt='',input_prompt='FULL WINDOW BODY'))
        assert bindings['task']['context_visible'] is True
    asyncio.run(run())


def test_missing_binding_publishes_failed_required_contribution():
    async def run():
        plugin=ArgusPlugin('argus',SimpleNamespace(bindings={}),TaskChannelRegistry());published=[]
        async def publish(event):published.append(event)
        plugin.bind_publisher(publish)
        await plugin.consume('user-input',UserInputEvent(task_id='missing',prompt='short'))
        assert published and published[0].status=='failed'
    asyncio.run(run())


def test_short_trigger_cannot_acknowledge_unseen_body(monkeypatch):
    from agent_app.plugins.argus.tools import ArgusTool
    plugin,binding,records,requests=fixture(monkeypatch)
    binding.update(view_version=3,prompt='short subject',observation_prompt='FULL WINDOW BODY',
        message_refs={'observation':'m1'},source_refs={},delivered=False)
    from agent_app.plugins.argus.references import References
    plugin.bridge.references=References(plugin.bridge)
    records.append(SimpleNamespace(type='argus.references',task_id=None,payload={'messages':{'observation':'m1'},'sources':{}}))
    result=asyncio.run(ArgusTool(plugin,'argus_commit').ainvoke(
        {'results':[{'ref':'m1','status':'handled'}]},task_id='task',task_messages=(Message('user','short subject'),)))
    assert not result.success and 'Full frozen content' in result.error
    assert not requests


def test_visibility_checks_all_serialized_user_contexts():
    from agent_app.plugins.argus.presentation import observations_visible
    import json
    body='ARGUS CURRENT MESSAGES\\n{"messages":[{"text":"完整正文"}]}'
    def envelope(text):return '<plugin_context>\n'+json.dumps([{'content':text}])+'\n</plugin_context>'
    assert observations_visible(body,envelope('older task')+'\n'+envelope(body))
    assert not observations_visible(body,envelope('older task')+'\nshort trigger')


def valid_wake():
    item={'id':'work','source_key':'source','message_id':'platform','kind':'message.observed','revision':1,
        'observed_at':'2026-11-05T00:00:00Z','content':{'text':'original'},'detail':{}}
    return {'channel_id':'channel','wake_id':'wake','binding_id':'binding','channel_version':1,
        'session_id':'session','session_key':'slot','rule_sdk':{},'argus_url':'http://localhost:8770',
        'channel_snapshot':{'id':'channel','version':1,'title':'task','goal':'goal','progress':'',
            'lifecycle':'active','source_keys':['source'],'rule_source':''},'items':[item]}


def test_window_cannot_replace_work_content_under_the_same_id():
    value=valid_wake()
    value['window_items']=[value['items'][0] | {'content':{'text':'different from the frozen work'}}]
    with pytest.raises(ValueError,match='work observation'):
        WakeRequest.model_validate(value)


@pytest.mark.parametrize('case',['missing_work','duplicate','foreign_source','history_overlap','too_large'])
def test_window_wire_scope_and_budget(case):
    value=valid_wake();item=value['items'][0]
    if case=='missing_work':value['window_items']=[item | {'id':'other'}]
    elif case=='duplicate':value['window_items']=[item,item]
    elif case=='foreign_source':value['history_items']=[item | {'id':'history','source_key':'foreign'}]
    elif case=='history_overlap':value['history_items']=[item]
    else:value['window_items']=[item,item | {'id':'other','content':{'text':'x'*(512*1024)}}]
    with pytest.raises(ValueError):WakeRequest.model_validate(value)
