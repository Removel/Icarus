"""Persisted source windows wait for a pause, not for a special message count."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from apps.argus.test.argus_app.infrastructure.test_store import store, clock
from argus_app.infrastructure.store import observed_items
from sqlalchemy import update


async def put(store, clock, mid, text='fragment', source='qq:100:private:200'):
    return await store.ingest(source,mid,'message.observed',{'text':text,'sender_id':'200'})


async def test_quiet_window_keeps_all_fragments_in_order(store,clock):
    first=await put(store,clock,'1')
    assert hasattr(store,'message_window'),'source-window scheduling is missing'
    assert await store.message_window(first.id,quiet_seconds=5,max_wait_seconds=30)==[]
    for n in range(2,7):
        clock.value+=timedelta(seconds=2)
        await put(store,clock,str(n))
    assert await store.message_window(first.id,quiet_seconds=5,max_wait_seconds=30)==[]
    clock.value+=timedelta(seconds=5)
    window=await store.message_window(first.id,quiet_seconds=5,max_wait_seconds=30)
    assert [row.message_id for row in window]==['1','2','3','4','5','6']
    assert len({row.detail['message_window'] for row in window})==1
    clock.value+=timedelta(seconds=1)
    other=await put(store,clock,'7')
    assert [row.id for row in await store.window_members(window[-1].id)]==[row.id for row in window]
    assert await store.message_window(other.id,quiet_seconds=5,max_wait_seconds=30)==[]


async def test_max_wait_and_capacity_do_not_drop_later_messages(store,clock):
    first=await put(store,clock,'1')
    assert hasattr(store,'message_window')
    for n in range(2,17):
        clock.value+=timedelta(seconds=2)
        await put(store,clock,str(n))
    window=await store.message_window(first.id,quiet_seconds=5,max_wait_seconds=30)
    assert len(window)==16  # Even with arrivals every two seconds, thirty seconds is enough.
    for n in range(17,39):await put(store,clock,str(n))
    later=await store.observations([first.source_key],limit=1)
    assert await store.message_window(later[0].id,quiet_seconds=5,max_wait_seconds=30)==[]
    candidates=await store.discovery_candidates(limit=50)
    unassigned=[x for x in candidates if 'message_window' not in x.detail]
    window2=await store.message_window(unassigned[0].id,quiet_seconds=5,max_wait_seconds=30)
    assert len(window2)==20
    clock.value+=timedelta(seconds=5)
    remaining=await store.message_window(later[0].id,quiet_seconds=5,max_wait_seconds=30)
    assert len(remaining)==2
    assert len({x.id for x in window+window2+remaining})==38


async def test_other_sources_and_old_terminal_messages_are_not_merged(store,clock):
    old=await put(store,clock,'old')
    await store.record_discovery(old.id,{'action':'ignore','reason':'old terminal'})
    current=await put(store,clock,'current')
    other=await put(store,clock,'other',source='qq:100:group:42')
    assert hasattr(store,'message_window')
    clock.value+=timedelta(seconds=5)
    window=await store.message_window(current.id,quiet_seconds=5,max_wait_seconds=30)
    assert [x.id for x in window]==[current.id]
    assert [x.id for x in await store.message_window(other.id,quiet_seconds=5,max_wait_seconds=30)]==[other.id]
    assert 'message_window' not in (await store.observation(old.id)).detail


async def test_new_channel_waits_until_whole_window_has_been_decided(store,clock):
    first=await put(store,clock,'1');second=await put(store,clock,'2')
    assert hasattr(store,'message_window')
    window=await store.message_window(first.id,quiet_seconds=0,max_wait_seconds=30)
    channel_id=await store.record_discovery(first.id,{'action':'new','reason':'needs response'},rule_path='seed.py',rule_hash='seed')
    ch=await store.channel(channel_id)
    assert await store.begin_round(ch.id) is None,'must not deliver only the seed while more window body is unjudged'
    assert hasattr(store,'decide_window')
    assert await store.decide_window(ch.id,[(second.id,{'passed':False,'reason':'statement completes the account'})],ch.version)
    round_=await store.begin_round(ch.id)
    assert round_['observation_ids']==[first.id]
    assert round_['window_observation_ids']==[first.id,second.id]


async def test_window_decision_conflict_rolls_back_earlier_items(store,clock):
    first=await put(store,clock,'1');second=await put(store,clock,'2')
    ch=await store.create_channel('task','goal',[first.source_key])
    assert hasattr(store,'decide_window')
    before=(await store.channel(ch.id)).model_dump()
    assert not await store.decide_window(ch.id,[(first.id,{'passed':True,'reason':'yes'}),('missing',{'passed':False,'reason':'no'})],ch.version)
    assert (await store.channel(ch.id)).model_dump()==before
