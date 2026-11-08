"""Running inbox claims immutable batches without changing the initial wake."""
import copy

import pytest

from apps.argus.test.argus_app.infrastructure.test_store import store,control,passed_round


async def supplemental(store,ch,text='supplement'):
    row=await store.ingest('qq:1',text,'message.observed',{'text':text})
    assert await store.decide(ch.id,row.id,ch.version,{'passed':True,'reason':'same matter'})
    return row


async def test_batch_is_idempotent_and_commit_covers_claimed_union(store):
    ch,items,round_=await passed_round(store)
    original=copy.deepcopy(round_)
    await control(store,round_,'agent.delivered',{'observation_ids':[items[0].id]})
    extra=await supplemental(store,ch)
    assert hasattr(store,'inbox_state'),'running inbox is missing'
    state=await store.inbox_state(round_['binding_id'])
    assert state['pending_count']==1
    row,result=await control(store,round_,'agent.batch_requested',{'limit':20},rid='receive-one')
    assert result['applied'] and result['items'][0]['id']==extra.id
    assert await store.apply_control(row.id)==result
    current=(await store.channel(ch.id)).runtime['round']
    assert current['observation_ids']==original['observation_ids']
    assert current['wake_id']==original['wake_id']
    assert (await store.inbox_state(round_['binding_id']))['pending_count']==0
    _,bad=await control(store,round_,'agent.commit_requested',{
        'expected_version':ch.version,'results':[{'observation_id':items[0].id,'status':'handled'}]})
    assert not bad['applied']
    _,not_seen=await control(store,round_,'agent.commit_requested',{'expected_version':ch.version,
        'results':[{'observation_id':x,'status':'handled'} for x in (items[0].id,extra.id)]})
    assert not not_seen['applied'] and not_seen['code']=='events_not_delivered'
    await control(store,round_,'agent.delivered',{'observation_ids':[extra.id]})
    later=await supplemental(store,ch,'later unclaimed')
    _,done=await control(store,round_,'agent.commit_requested',{'expected_version':ch.version,
        'results':[{'observation_id':x,'status':'handled'} for x in (items[0].id,extra.id)]})
    assert done['committed'] is True
    assert set((await store.channel(ch.id)).runtime['pending'])=={later.id}
    _,late=await control(store,round_,'agent.batch_requested',{'limit':20},rid='late')
    assert not late['applied']


async def test_receive_cannot_claim_other_channel_or_unaccepted_work(store):
    ch,items,round_=await passed_round(store)
    other=await store.create_channel('other','unrelated',['other'])
    row=await store.ingest('other','1','message.observed',{'text':'not this matter'})
    await store.decide(other.id,row.id,other.version,{'passed':True,'reason':'other'})
    pending=await store.ingest('qq:1','undecided','message.observed',{'text':'not yet judged'})
    assert hasattr(store,'inbox_state')
    assert (await store.inbox_state(round_['binding_id']))['pending_count']==0
    _,result=await control(store,round_,'agent.batch_requested',{'limit':20},rid='receive')
    assert result['applied'] and result['items']==[]


async def test_receive_capacity_leaves_unclaimed_work_pending(store):
    ch,items,round_=await passed_round(store)
    for n in range(5):
        await supplemental(store,ch,'extra-'+str(n))
        _,result=await control(store,round_,'agent.batch_requested',{'limit':1},rid='batch-'+str(n))
        if n<4:
            assert result['applied'] and len(result['items'])==1
        else:
            assert not result['applied'] and result['code']=='inbox_batch_capacity'
    current=await store.channel(ch.id)
    assert len(current.runtime['round']['batches'])==4
    assert len(current.runtime['pending'])==6
    assert (await store.inbox_state(round_['binding_id']))['pending_count']==1


async def test_claim_and_commit_race_never_loses_unclaimed_message(store):
    import asyncio
    ch,items,round_=await passed_round(store)
    await control(store,round_,'agent.delivered',{'observation_ids':[items[0].id]})
    extra=await supplemental(store,ch)
    (claim_row,claimed),(commit_row,committed)=await asyncio.gather(
        control(store,round_,'agent.batch_requested',{'limit':20},rid='racing-claim'),
        control(store,round_,'agent.commit_requested',{'expected_version':ch.version,
            'results':[{'observation_id':items[0].id,'status':'handled'}]},rid='racing-commit'))
    current=await store.channel(ch.id)
    assert extra.id in current.runtime['pending']
    if committed.get('committed'):
        assert not claimed['applied'] and current.runtime['round'] is None
    else:
        assert claimed['applied'] and current.runtime['round']['batches'][0]['observation_ids']==[extra.id]
