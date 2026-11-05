"""Naming runs after new decision but before the Channel creation transaction."""
from types import SimpleNamespace
import asyncio

import pytest

from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleFiles


class Store:
    def __init__(self):
        self.saved=None;self.token=None;self.created=[];self.active=False
    def discovered_channel_id(self,current):return 'channel'
    async def claim_channel_name(self,id):
        if self.token:return None
        self.token='claim';self.active=True;return self.token
    async def channel_name(self,id):return self.saved
    async def finish_channel_name(self,id,token,*,title=None,error_code=None):
        self.active=False;self.saved={'status':'completed' if title else 'failed','title':title,'error_code':error_code}
    async def record_discovery(self,id,decision,**kwargs):
        self.created.append((decision,kwargs));return 'channel'


async def test_new_channel_is_named_before_store_creation(tmp_path):
    store=Store();calls=[]
    async def name(text,*,reason):
        assert not store.created and store.active
        calls.append(text);return '图片查看工具需求'
    plugin=WorkflowRules('workflow',store,SimpleNamespace(),RuleFiles(tmp_path))
    plugin.channel_namer=name
    async def publish(event):pass
    plugin.bind_publisher(publish)
    item=SimpleNamespace(id='item',content={'text':'能不能帮忙开发image view'})
    await plugin._record_discovery(item,{'action':'new','reason':'needs response'})
    assert calls==['能不能帮忙开发image view']
    assert store.created[0][1]['title']=='图片查看工具需求'
    assert store.saved['status']=='completed'


async def test_name_failure_does_not_drop_channel_creation(tmp_path):
    store=Store()
    async def name(*args,**kwargs):raise TimeoutError()
    plugin=WorkflowRules('workflow',store,SimpleNamespace(),RuleFiles(tmp_path));plugin.channel_namer=name
    async def publish(event):pass
    plugin.bind_publisher(publish)
    await plugin._record_discovery(SimpleNamespace(id='item',content={'text':'hello'}),{'action':'new','reason':'needs response'})
    assert len(store.created)==1 and store.created[0][1].get('title') is None
    assert store.saved['error_code']=='timeout'


async def test_completed_name_is_reused_without_another_model_call(tmp_path):
    store=Store();store.token='claim';store.saved={'status':'completed','title':'已有标题'}
    async def name(*args,**kwargs):raise AssertionError('must reuse name')
    plugin=WorkflowRules('workflow',store,SimpleNamespace(),RuleFiles(tmp_path));plugin.channel_namer=name
    async def publish(event):pass
    plugin.bind_publisher(publish)
    await plugin._record_discovery(SimpleNamespace(id='item',content={'text':'hello'}),{'action':'new','reason':'needs response'})
    assert store.created[0][1]['title']=='已有标题'


async def test_recovered_new_fallback_names_outside_discovery_lock(tmp_path):
    store=Store()
    item=SimpleNamespace(id='item',source_key='qq:1:group:2',content={'text':'new task'},
        detail={'discovery':{'status':'fallback_pending','fallback':{'status':'completed',
            'token':'fallback','candidate_versions':{},'decision':{'action':'new','reason':'response'}}}})
    async def observation(id):return item
    async def channels():return []
    async def ready(id):return True
    store.observation=observation;store.channels=channels;store.discovery_ready=ready
    plugin=WorkflowRules('workflow',store,SimpleNamespace(),RuleFiles(tmp_path))
    async def name(text,*,reason):
        assert not plugin._discovery_lock.locked(), 'naming blocks unrelated discovery'
        return '任务名称'
    plugin.channel_namer=name
    async def publish(event):pass
    plugin.bind_publisher(publish)
    await plugin.discover(item)
    assert store.saved['status']=='completed'


async def test_inflight_name_does_not_reclassify_same_observation(tmp_path):
    store=Store()
    item=SimpleNamespace(id='item',source_key='qq:1:group:2',content={'text':'new task'},detail={},
        model_dump=lambda **kwargs:{'id':'item','content':{'text':'new task'}})
    async def observation(id):return item
    async def channels():return []
    async def ready(id):return True
    store.observation=observation;store.channels=channels;store.discovery_ready=ready
    class Engine:
        calls=0
        async def run(self,*args):
            self.calls+=1
            return {'action':'new','reason':'response'}
    engine=Engine();entered=asyncio.Event();release=asyncio.Event()
    plugin=WorkflowRules('workflow',store,engine,RuleFiles(tmp_path))
    async def name(text,*,reason):
        entered.set();await release.wait();return '任务名称'
    plugin.channel_namer=name
    async def publish(event):pass
    plugin.bind_publisher(publish)
    first=asyncio.create_task(plugin.discover(item))
    try:
        await asyncio.wait_for(entered.wait(),2)
        await asyncio.wait_for(plugin.discover(item),2)
        assert engine.calls==1,'pending naming must not repeat paid discovery'
    finally:
        release.set();await first
    assert len(store.created)==1
