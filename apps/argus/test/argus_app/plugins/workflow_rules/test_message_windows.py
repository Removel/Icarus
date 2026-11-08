"""A source window is judged together and only then made eligible for delivery."""
from datetime import timedelta

from argus_app.plugins.workflow_rules.plugin import WorkflowRules
from argus_app.rule_sdk import RuleFiles
from argus_app.contracts.models import Decision
from argus_app.contracts.capabilities import RuleResponse
from icarus.core.capabilities import CapabilityStatus
from apps.argus.test.argus_app.infrastructure.test_store import store, clock


async def test_all_fragments_are_judged_with_whole_body_before_one_notice(store,clock,tmp_path):
    texts=['服了','有个人上来就问','问有没有资金','6','我当时和老师做项目都不敢问有没有钱拿','神人多']
    items=[]
    for n,text in enumerate(texts):
        items.append(await store.ingest('qq:100:private:200',str(n),'message.observed',{'text':text,'sender_id':'200'}))
    ch=await store.create_channel('conversation','understand the current matter',[items[0].source_key],rule_path='rule.py')
    class Files:
        def read(self,*args):return 'rule'
    class Engine:
        async def evaluate(self,source,event,channel,call):
            data=await call('semantic',{'text':event.content['text'],'criterion':'is this related?'})
            return Decision(passed=data['match'],reason='related')
    plugin=WorkflowRules('workflow',store,Engine(),Files())
    plugin.quiet_seconds=5;plugin.max_wait_seconds=30
    seen=[];notices=[]
    async def request(req):
        window=req.args.get('context',{}).get('current_window',[])
        seen.append([x['text'] for x in window])
        return RuleResponse(correlation_id=req.correlation_id,status=CapabilityStatus.OK,data={'match':True,'reason':'same story'})
    async def publish(event):
        # Nothing may become visible while the rest are still being classified.
        current=await store.channel(ch.id)
        assert len(current.runtime['pending'])==6
        notices.append(event)
    plugin.request=request;plugin.bind_publisher(publish)
    await plugin.process_channel(ch.id)
    assert not seen and not notices,'private chat must not bypass the quiet window'
    clock.value+=timedelta(seconds=5)
    await plugin.process_channel(ch.id)
    assert seen==[texts]*6
    assert len(notices)==1
    round_=await store.begin_round(ch.id)
    assert round_['observation_ids']==[x.id for x in items]
    assert round_['window_observation_ids']==[x.id for x in items]


async def test_interleaved_sources_do_not_fill_capacity_with_partial_windows(store,clock,tmp_path):
    from apps.argus.test.argus_app.infrastructure.test_store import control
    sources=[f'qq:100:group:{i+1}' for i in range(16)]
    for n in range(20):
        for source in sources:
            await store.ingest(source,str(n),'message.observed',{'text':f'part {n}','sender_id':'member'})
    ch=await store.create_channel('many sources','process coherent windows',sources,rule_path='rule.py')
    class Files:
        def read(self,*args):return 'rule'
    class Engine:
        async def evaluate(self,*args):return Decision(passed=True,reason='related')
    plugin=WorkflowRules('workflow',store,Engine(),Files());plugin.quiet_seconds=5
    async def publish(event):pass
    plugin.bind_publisher(publish);clock.value+=timedelta(seconds=5)
    delivered=set()
    for _ in range(20):
        await plugin.process_channel(ch.id)
        current=await store.channel(ch.id)
        by_source={source:0 for source in sources}
        for id in current.runtime['pending']:
            row=await store.observation(id);by_source[row.source_key]+=1
        assert all(count in (0,20) for count in by_source.values()),'a scan budget split a source window'
        round_=await store.begin_round(ch.id)
        if not round_:
            assert len(delivered)==320,'capacity stranded partial windows with no drainable work'
            break
        ids=round_['observation_ids'];assert not delivered.intersection(ids)
        await control(store,round_,'agent.delivered',{'observation_ids':ids})
        current=await store.channel(ch.id)
        _,result=await control(store,round_,'agent.commit_requested',{
            'expected_version':current.version,'results':[{'observation_id':id,'status':'handled'} for id in ids]})
        assert result.get('committed') is True
        delivered.update(ids)
    assert len(delivered)==320
