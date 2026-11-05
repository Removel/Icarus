"""Actual evidence is direct; orchestration identifiers never become model homework."""
import asyncio
import importlib.util
import json
from types import SimpleNamespace

from . import test_commit_tools


def test_projection_exposes_semantics_not_internal_ids():
    assert importlib.util.find_spec('agent_app.plugins.argus.presentation'), 'missing semantic view'
    from agent_app.plugins.argus.presentation import project_observation, render_round, semantic_subject
    item = dict(id='internal-observation', source_key='internal-source', message_id='platform-message',
        observed_at='2026-11-03T12:30:00Z', content={'text':'Build the picture viewer','sender_id':'sender','sender_name':'Member'},
        detail={'source':{'platform':'qq','kind':'group','region_id':'42','account_id':'100'},
                'discovery':{'channel_id':'internal-channel'}})
    value = project_observation(item, ref='m1', role='work')
    assert value['text'] == 'Build the picture viewer'
    assert value['sender']['name'] == 'Member'
    assert value['ref'] == 'm1'
    assert 'internal-' not in json.dumps(value)
    wake = dict(items=[item], channel_snapshot={'goal':'Confirm requirements','progress':''},
        channel_id='internal-channel',binding_id='internal-binding',rule_sdk={'secret':'sdk'})
    text = render_round(wake, message_refs={'internal-observation':'m1'}, source_refs={'internal-source':'s1'})
    assert '42' in text and 'm1' in text and 's1' in text
    assert 'internal-' not in text and 'rule_sdk' not in text
    assert semantic_subject([item]) == 'Member: Build the picture viewer'


def test_each_message_has_the_original_platform_reply_target():
    from agent_app.plugins.argus.presentation import render_round
    items=[]
    for id,kind,region,sender in [('a','private','200','200'),('b','group','42','300')]:
        items.append({'id':id,'source_key':'key-'+id,'message_id':'platform-'+id,
            'content':{'text':'question','sender_id':sender},
            'detail':{'source':{'platform':'qq','kind':kind,'region_id':region,'account_id':'100'}}})
    wake={'items':items,'channel_snapshot':{}}
    view=json.loads(render_round(wake,message_refs={'a':'m1','b':'m2'},source_refs={'key-a':'s1','key-b':'s2'}).split('\n',1)[1])
    private,group=(m['reply_target'] for m in view['messages'])
    assert private=={'source':'s1','platform':'qq','kind':'private','account_id':'100',
        'user_id':'200','platform_message_id':'platform-a'}
    assert group=={'source':'s2','platform':'qq','kind':'group','account_id':'100',
        'group_id':'42','platform_message_id':'platform-b'}
    assert '300' not in str(group), 'group reply must not become a private reply to the sender'
    assert not any(word in json.dumps(view) for word in ('key-a','key-b','rule_sdk','binding_id'))


def test_feishu_reply_target_preserves_current_message_thread():
    from agent_app.plugins.argus.presentation import render_round
    item={'id':'a','source_key':'key','message_id':'message','content':{'text':'question'},
        'detail':{'source':{'platform':'feishu','kind':'group','region_id':'chat','tenant_id':'tenant'},
            'thread_id':'thread','reply_to':'quoted-older-message'}}
    view=json.loads(render_round({'items':[item],'channel_snapshot':{}},message_refs={'a':'m1'},source_refs={'key':'s1'}).split('\n',1)[1])
    target=view['messages'][0]['reply_target']
    assert target['chat_id']=='chat' and target['thread_id']=='thread'
    assert target['platform_message_id']=='message'
    assert 'quoted-older-message' not in str(target)


def test_unknown_source_does_not_invent_a_reply_destination():
    from agent_app.plugins.argus.presentation import render_round
    item={'id':'a','source_key':'qq:looks:like:a-key','message_id':'message',
        'content':{'text':'question','sender_id':'sender'},'detail':{}}
    view=json.loads(render_round({'items':[item],'channel_snapshot':{}},message_refs={'a':'m1'},source_refs={item['source_key']:'s1'}).split('\n',1)[1])
    assert view['messages'][0]['reply_target']=={'source':'s1','platform':'unknown',
        'kind':'unknown','platform_message_id':'message'}


def test_v2_commit_resolves_only_persisted_current_refs(monkeypatch):
    plugin, binding, records, requests = test_commit_tools.fixture(monkeypatch)
    binding.update(view_version=2, message_refs={'observation':'m12'}, source_refs={})
    from agent_app.plugins.argus.references import References
    plugin.bridge.references = References(plugin.bridge)
    records.append(SimpleNamespace(type='argus.references',task_id=None,
        payload={'messages':{'observation':'m12'},'sources':{}}))
    from agent_app.plugins.argus.tools import ArgusTool
    async def run():
        tool = ArgusTool(plugin, 'argus_commit')
        rejected = await tool.ainvoke({'results':[{'ref':'observation','status':'handled'}]},task_id='task')
        assert not rejected.success, 'raw ID must not be accepted in v2'
        accepted = await tool.ainvoke({'results':[{'ref':'m12','status':'handled'}]},task_id='task')
        assert accepted.success, accepted.error
        assert 'version' not in accepted.output
        assert accepted.output['committed'] is True
    asyncio.run(run())


def test_projection_preserves_temporary_private_reply_target():
    from agent_app.plugins.argus.presentation import source_view
    item={'source_key':'qq:100:private:200','content':{'message_type':'private'},
        'detail':{'protocol':'onebot','self_id':'100','peer_id':'200','temp_group_id':'42'}}
    assert source_view(item).get('temp_group_id')=='42'


def test_legacy_feishu_projection_retains_chat_target():
    from agent_app.plugins.argus.presentation import source_view
    item={'source_key':'feishu:tenant:chat:chat-id','content':{'message_type':'text'},
        'detail':{'protocol':'feishu-v2','tenant_key':'tenant'}}
    assert source_view(item)['region_id']=='chat-id'


def test_old_source_alias_does_not_authorize_resubscription(monkeypatch):
    plugin,binding,records,requests=test_commit_tools.fixture(monkeypatch)
    binding.update(view_version=2,message_refs={'observation':'m1'},
        source_refs={'revoked':'s1','current':'s2'})
    binding['wake']['channel_snapshot']={'source_keys':['current']}
    from agent_app.plugins.argus.references import References
    plugin.bridge.references=References(plugin.bridge)
    records.append(SimpleNamespace(type='argus.references',task_id=None,
        payload={'messages':{'observation':'m1'},'sources':{'revoked':'s1','current':'s2'}}))
    from agent_app.plugins.argus.tools import ArgusTool
    result=asyncio.run(ArgusTool(plugin,'argus_commit').ainvoke(
        {'results':[{'ref':'m1','status':'handled'}],'source_refs':['s1']},task_id='task'))
    assert not result.success and 'source' in result.error.lower()
    assert not requests


def test_direct_commit_requires_durable_reference_evidence(monkeypatch):
    plugin,binding,records,requests=test_commit_tools.fixture(monkeypatch)
    binding.update(view_version=2,message_refs={'observation':'m1'},source_refs={})
    from agent_app.plugins.argus.references import References
    plugin.bridge.references=References(plugin.bridge)
    from agent_app.plugins.argus.tools import ArgusTool
    result=asyncio.run(ArgusTool(plugin,'argus_commit').ainvoke(
        {'results':[{'ref':'m1','status':'handled'}]},task_id='task'))
    assert not result.success and 'reference_state_corrupt' in result.error
    assert not requests
