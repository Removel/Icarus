"""Real PostgreSQL intent claims, cached decisions, and crash recovery boundaries."""
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text

from argus_app.contracts.models import DomainError
from argus_app.infrastructure.store import Store, observed_items

NEW = {'action': 'new', 'reason': 'explicit request'}


@pytest_asyncio.fixture
async def store(database_url):
    value = Store(database_url)
    await value.start()
    try:
        yield value
    finally:
        await value.close()


async def incoming(store, text_value='请跟进接口评审'):
    return await store.ingest('qq:123:group:456', 'message-1', 'message.observed', {'text': text_value})


def require_api(store):
    assert callable(getattr(store, 'claim_discovery_fallback', None)), 'durable fallback claim is missing'
    assert callable(getattr(store, 'finish_discovery_fallback', None)), 'durable fallback result is missing'


async def expire(store, item):
    row = await store.observation(item.id)
    detail = row.detail
    detail['discovery']['next_retry_at'] = '2020-01-01T00:00:00+00:00'
    detail['discovery']['fallback']['deadline_at'] = '2020-01-01T00:00:00+00:00'
    async with store.engine.begin() as connection:
        await connection.execute(observed_items.update().where(observed_items.c.id == item.id).values(detail=detail))


async def test_concurrent_stores_claim_at_most_one_intent(store, database_url):
    require_api(store)
    item = await incoming(store)
    other = Store(database_url)
    try:
        tokens = await asyncio.gather(store.claim_discovery_fallback(item.id, {}),
                                     other.claim_discovery_fallback(item.id, {}))
        assert sum(token is not None for token in tokens) == 1
        assert await store.claim_discovery_fallback(item.id, {}) is None
        row = await store.observation(item.id)
        assert row.detail['discovery']['status'] == 'fallback_pending'
        assert row.detail['discovery']['fallback']['status'] == 'started'
        assert await store.discovery_candidates() == []
        async with store.engine.connect() as connection:
            tables = set((await connection.execute(text('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'))).scalars())
        assert tables == {'observed_items', 'channels'}
    finally:
        await other.close()


async def test_saved_result_replays_after_store_restart_without_new_claim(store, database_url):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    recovered = Store(database_url)
    try:
        assert await recovered.claim_discovery_fallback(item.id, {}) is None
        cached = (await recovered.observation(item.id)).detail['discovery']['fallback']
        assert cached['decision'] == NEW
        assert [x.id for x in await recovered.discovery_candidates()] == [item.id]
        channel_id = await recovered.record_discovery(item.id, cached['decision'],
            candidate_versions={}, fallback_token=token, rule_path='initial.py', rule_hash='hash')
        assert channel_id
        assert await recovered.record_discovery(item.id, NEW, fallback_token=token) == channel_id
        assert len(await recovered.channels()) == 1
        assert (await recovered.observation(item.id)).detail['discovery']['fallback']['status'] == 'completed'
    finally:
        await recovered.close()


async def test_unfinished_or_expired_intent_never_reclaims_budget(store):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await expire(store, item)
    assert [x.id for x in await store.discovery_candidates()] == [item.id]
    assert await store.claim_discovery_fallback(item.id, {}) is None
    await store.finish_discovery_fallback(item.id, token, error_code='outcome_unknown')
    await store.record_discovery(item.id, {'error': 'outcome_unknown'})
    state = (await store.observation(item.id)).detail['discovery']
    assert state['fallback']['status'] == 'unknown'
    assert state['status'] == 'error'
    assert await store.claim_discovery_fallback(item.id, {}) is None


async def test_late_model_result_cannot_be_applied(store):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await expire(store, item)
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    state = (await store.observation(item.id)).detail['discovery']['fallback']
    assert state['status'] == 'unknown'
    with pytest.raises(DomainError, match='discovery_fallback_conflict'):
        await store.record_discovery(item.id, NEW, fallback_token=token, rule_path='x', rule_hash='h')
    assert await store.channels() == []


async def test_conflicting_tokens_and_results_are_rejected(store):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    with pytest.raises(DomainError, match='discovery_fallback_conflict'):
        await store.finish_discovery_fallback(item.id, 'other-token', decision=NEW)
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    with pytest.raises(DomainError, match='discovery_fallback_conflict'):
        await store.finish_discovery_fallback(item.id, token, decision={'action': 'ignore', 'reason': 'changed'})
    with pytest.raises(DomainError, match='discovery_fallback_conflict'):
        await store.record_discovery(item.id, {'action': 'ignore', 'reason': 'changed'}, fallback_token=token)
    with pytest.raises(DomainError, match='discovery_fallback_conflict'):
        await store.record_discovery(item.id, NEW, fallback_token='other-token')
    assert await store.channels() == []


async def test_candidate_changes_reject_cached_decision(store):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    channel = await store.create_channel('other request', 'other goal', [item.source_key])
    assert await store.record_discovery(item.id, NEW, candidate_versions={}, fallback_token=token,
        rule_path='x', rule_hash='h') is None
    assert [c.id for c in await store.channels()] == [channel.id]
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == 'error'
    assert state['fallback']['status'] == 'completed'
    assert await store.claim_discovery_fallback(item.id, {channel.id: channel.version}) is None


async def test_new_revision_has_own_budget_old_result_cannot_create(store):
    require_api(store)
    first = await incoming(store)
    token = await store.claim_discovery_fallback(first.id, {})
    latest = await incoming(store, '更正：请跟进新的评审事项')
    await store.finish_discovery_fallback(first.id, token, decision=NEW)
    assert await store.record_discovery(first.id, NEW, candidate_versions={}, fallback_token=token,
        rule_path='x', rule_hash='h') is None
    assert await store.channels() == []
    assert await store.claim_discovery_fallback(first.id, {}) is None
    assert await store.claim_discovery_fallback(latest.id, {})


@pytest.mark.parametrize('error_code,status', [('timeout', 'unknown'), ('cancelled', 'unknown'),
    ('outcome_unknown', 'unknown'), ('invalid_output', 'failed'), ('unavailable', 'failed')])
async def test_failure_status_and_budget_survive_ordinary_discovery_update(store, error_code, status):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await store.finish_discovery_fallback(item.id, token, error_code=error_code)
    await store.record_discovery(item.id, {'error': error_code})
    state = (await store.observation(item.id)).detail['discovery']
    assert state['fallback']['status'] == status
    assert state['fallback']['error_code'] == error_code
    assert await store.claim_discovery_fallback(item.id, {}) is None
    assert await store.channels() == []


@pytest.mark.parametrize('ordinary', [
    {'action': 'ignore', 'reason': 'earlier primary'},
    {'action': 'defer', 'reason': 'earlier low-confidence primary'},
])
async def test_completed_unapplied_fallback_fences_ordinary_primary_result(store, ordinary):
    require_api(store)
    item = await incoming(store)
    token = await store.claim_discovery_fallback(item.id, {})
    await store.finish_discovery_fallback(item.id, token, decision=NEW)
    assert await store.record_discovery(item.id, ordinary, candidate_versions={}) is None
    state = (await store.observation(item.id)).detail['discovery']
    assert state['status'] == 'fallback_pending'
    assert state['fallback']['decision'] == NEW
    channel_id = await store.record_discovery(item.id, NEW, fallback_token=token,
        rule_path='initial.py', rule_hash='hash')
    assert channel_id
    assert (await store.observation(item.id)).detail['discovery']['status'] == 'created'


async def test_claim_rejects_terminal_or_outdated_candidate_snapshot(store):
    require_api(store)
    item = await incoming(store)
    channel = await store.create_channel('existing', 'goal', [item.source_key])
    assert await store.claim_discovery_fallback(item.id, {}) is None
    await store.record_discovery(item.id, {'action': 'ignore', 'reason': 'not a request'})
    assert await store.claim_discovery_fallback(item.id, {channel.id: channel.version}) is None
