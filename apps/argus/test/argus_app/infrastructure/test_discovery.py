"""Discovery is an atomic operation on the existing two tables, not a third entity."""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from argus_app.infrastructure.store import Store, observed_items


@pytest_asyncio.fixture
async def discovery_store(database_url):
    store = Store(database_url)
    await store.start()
    try:
        yield store
    finally:
        await store.close()


async def incoming(store, message_id='first'):
    return await store.ingest('qq:123:group:456', message_id, 'message.observed',
                              {'text': '请帮我跟进接口评审'})


@pytest.mark.asyncio
async def test_discovery_api_is_available_without_additional_tables(discovery_store):
    assert callable(getattr(discovery_store, 'discovery_candidates', None)), 'Unsubscribed observations need durable discovery recovery'
    assert callable(getattr(discovery_store, 'record_discovery', None)), 'Creation must atomically seed the first event'
    async with discovery_store.engine.connect() as conn:
        names = set((await conn.execute(text('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'))).scalars())
    assert names == {'observed_items', 'channels'}


@pytest.mark.asyncio
async def test_discovery_creates_channel_seeds_first_event_and_is_idempotent(discovery_store):
    item = await incoming(discovery_store)
    assert [row.id for row in await discovery_store.discovery_candidates()] == [item.id]
    channel_id = await discovery_store.record_discovery(item.id, {'action': 'new', 'reason': 'explicit request'},
                                                       rule_path='bootstrap.py', rule_hash='seed-hash')
    channel = await discovery_store.channel(channel_id)
    assert channel.goal == item.content['text']
    assert channel.rule_path == 'bootstrap.py'
    assert channel.runtime['pending'][item.id]['status'] == 'passed'
    assert (await discovery_store.begin_round(channel_id))['observation_ids'] == [item.id]
    assert await discovery_store.record_discovery(item.id, {'action': 'new', 'reason': 'duplicate'}) == channel_id
    assert len(await discovery_store.channels()) == 1
    assert await discovery_store.discovery_candidates() == []
    result = (await discovery_store.observation(item.id)).detail['discovery']
    assert result['status'] == 'created' and result['channel_id'] == channel_id


@pytest.mark.asyncio
async def test_channel_name_claim_is_once_and_saved_before_creation(discovery_store):
    item = await incoming(discovery_store)
    token = await discovery_store.claim_channel_name(item.id)
    assert token
    assert await discovery_store.claim_channel_name(item.id) is None
    await discovery_store.finish_channel_name(item.id,token,title='接口评审跟进')
    assert (await discovery_store.channel_name(item.id))['title']=='接口评审跟进'
    channel_id=await discovery_store.record_discovery(item.id,{'action':'new','reason':'request'},
        rule_path='bootstrap.py',rule_hash='seed',title='接口评审跟进')
    assert (await discovery_store.channel(channel_id)).title=='接口评审跟进'
    assert (await discovery_store.channel(channel_id)).goal==item.content['text']


@pytest.mark.asyncio
async def test_discovery_ignore_is_durable_without_creating_channel(discovery_store):
    item = await incoming(discovery_store)
    assert await discovery_store.record_discovery(item.id, {'action': 'ignore', 'reason': 'not a task'}) is None
    assert await discovery_store.channels() == []
    assert await discovery_store.discovery_candidates() == []


@pytest.mark.asyncio
async def test_concurrent_first_events_create_one_channel_per_source(discovery_store):
    first = await incoming(discovery_store, 'one')
    second = await incoming(discovery_store, 'two')
    values = await asyncio.gather(*(discovery_store.record_discovery(item.id,
        {'action': 'new', 'reason': 'request'}, rule_path='bootstrap.py', rule_hash='seed') for item in (first, second)))
    assert len([value for value in values if value is not None]) == 2
    assert values[0] == values[1]
    assert len(await discovery_store.channels()) == 1
    owner = (await discovery_store.channels())[0]
    # The first event is seeded; later facts remain readable by its regular rule.
    assert len(owner.runtime['pending']) == 1


@pytest.mark.asyncio
async def test_subscription_created_during_classification_suppresses_new_channel(discovery_store):
    item = await incoming(discovery_store)
    owner = await discovery_store.create_channel('existing', 'goal', [item.source_key])
    assert await discovery_store.record_discovery(item.id, {'action': 'new', 'reason': 'late classification'}, candidate_versions={}) is None
    assert [ch.id for ch in await discovery_store.channels()] == [owner.id]
    assert [row.id for row in await discovery_store.unread(owner.id)] == [item.id]


@pytest.mark.asyncio
async def test_paused_subscriber_suppresses_discovery(discovery_store):
    item = await incoming(discovery_store)
    owner = await discovery_store.create_channel('paused', 'goal', [item.source_key])
    async with discovery_store.engine.begin() as conn:
        await conn.execute(text("UPDATE channels SET lifecycle='paused' WHERE id=:id"), {'id': owner.id})
    assert await discovery_store.record_discovery(item.id, {'action': 'existing', 'channel_id': owner.id, 'reason': 'same paused task'}) is None
    assert len(await discovery_store.channels()) == 1


@pytest.mark.asyncio
async def test_discovery_errors_retry_bounded_and_never_become_ignore(discovery_store):
    item = await incoming(discovery_store)
    for _ in range(3):
        await discovery_store.record_discovery(item.id, {'error': 'semantic_unavailable'})
        row = await discovery_store.observation(item.id)
        assert row.detail['discovery']['status'] in {'error', 'exhausted'}
        async with discovery_store.engine.begin() as conn:
            detail = row.detail
            detail['discovery']['next_retry_at'] = '2020-01-01T00:00:00+00:00'
            await conn.execute(observed_items.update().where(observed_items.c.id == item.id).values(detail=detail))
    assert await discovery_store.discovery_candidates() == []
    assert (await discovery_store.observation(item.id)).detail['discovery']['attempt_count'] == 3
    assert await discovery_store.channels() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('previous_failure', [None, 'error', 'deferred'])
async def test_already_processed_request_never_rediscovers_after_source_removed(discovery_store, previous_failure):
    item = await incoming(discovery_store)
    if previous_failure == 'error':
        await discovery_store.record_discovery(item.id, {'error': 'temporary'})
    elif previous_failure == 'deferred':
        await discovery_store.record_discovery(item.id, {'action': 'defer', 'reason': 'later'})
    owner = await discovery_store.create_channel('owner', 'goal', [item.source_key])
    assert await discovery_store.decide(owner.id, item.id, owner.version, {'passed': False, 'reason': 'already known'})
    # Discovery must independently classify the event, rather than infer ownership from subscription.
    async with discovery_store.engine.begin() as conn:
        detail = (await discovery_store.observation(item.id)).detail
        if 'discovery' in detail:
            detail['discovery']['next_retry_at'] = '2020-01-01T00:00:00+00:00'
            await conn.execute(observed_items.update().where(observed_items.c.id == item.id).values(detail=detail))
    await discovery_store.record_discovery(item.id, {'action': 'existing', 'channel_id': owner.id, 'reason': 'known task'})
    async with discovery_store.engine.begin() as conn:
        await conn.execute(text("UPDATE channels SET subscriptions='{}',lifecycle='completed' WHERE id=:id"), {'id': owner.id})
    assert await discovery_store.discovery_candidates() == []
    assert (await discovery_store.observation(item.id)).detail['discovery']['status'] == 'existing'


@pytest.mark.asyncio
async def test_discovery_duplicate_does_not_bypass_retry_backoff(discovery_store):
    item = await incoming(discovery_store)
    await discovery_store.record_discovery(item.id, {'error': 'temporary'})
    await discovery_store.record_discovery(item.id, {'error': 'duplicate'})
    assert (await discovery_store.observation(item.id)).detail['discovery']['attempt_count'] == 1


@pytest.mark.asyncio
async def test_obsolete_revision_cannot_create_a_channel(discovery_store):
    first = await incoming(discovery_store)
    second = await discovery_store.ingest(first.source_key, first.message_id, first.kind, {'text': 'edited request'})
    assert [row.id for row in await discovery_store.discovery_candidates()] == [second.id]
    assert await discovery_store.record_discovery(first.id, {'action': 'new', 'reason': 'stale'}) is None
    assert await discovery_store.channels() == []
