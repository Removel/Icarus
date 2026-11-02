"""Atomic business completion against a disposable real PostgreSQL database."""
import asyncio

import pytest
from sqlalchemy import text

from apps.argus.test.argus_app.infrastructure.test_store import control, passed_round, store
from argus_app.infrastructure.store import Store


async def delivered(store, count=2):
    ch, items, round_ = await passed_round(store, count)
    await control(store, round_, 'agent.delivered', {'observation_ids': [x.id for x in items]})
    return ch, items, round_


def payload(ch, items, **changes):
    return {'expected_version': ch.version,
            'results': [{'observation_id': x.id, 'status': 'handled'} for x in items], **changes}


async def test_commit_applies_results_state_rule_and_close_atomically(store):
    ch, items, round_ = await delivered(store)
    digest = 'a' * 64
    row, result = await control(store, round_, 'agent.commit_requested', payload(
        ch, items, progress='review approved', rule_hash=digest,
        rule_path=f'{ch.id}/{digest}.py', lifecycle='completed'))
    assert result.get('committed') is True
    current = await store.channel(ch.id)
    assert (current.progress, current.rule_hash, current.lifecycle) == ('review approved', digest, 'completed')
    assert current.version == 2
    assert current.runtime['round'] is None
    assert current.runtime['pending'] == {}
    assert current.subscriptions == {}
    assert await store.apply_control(row.id) == result
    assert await store.commit_status(round_['binding_id'], row.message_id) == {'state': 'applied', 'result': result}


async def test_commit_unchanged_closes_without_version_increment(store):
    ch, items, round_ = await delivered(store)
    _, result = await control(store, round_, 'agent.commit_requested', payload(ch, items))
    assert result.get('committed') is True
    current = await store.channel(ch.id)
    assert current.version == ch.version
    assert current.runtime['round'] is None
    assert current.runtime['pending'] == {}


@pytest.mark.parametrize('changes', [
    {'expected_version': 999}, {'lifecycle': 'unknown'}, {'source_keys': [False]},
    {'rule_hash': 'bad'}, {'progress': None}, {'extra': 'forbidden'},
])
async def test_rejected_commit_preserves_entire_channel(store, changes):
    ch, items, round_ = await delivered(store)
    before = (await store.channel(ch.id)).model_dump()
    _, result = await control(store, round_, 'agent.commit_requested', payload(ch, items, **changes))
    assert result['applied'] is False
    assert (await store.channel(ch.id)).model_dump() == before


async def test_commit_requires_all_delivered_frozen_results(store):
    ch, items, round_ = await delivered(store)
    before = (await store.channel(ch.id)).model_dump()
    for results in [[], [{'observation_id': items[0].id, 'status': 'handled'}],
                    [{'observation_id': x.id, 'status': 'handled'} for x in items] +
                    [{'observation_id': items[0].id, 'status': 'handled'}]]:
        _, result = await control(store, round_, 'agent.commit_requested', payload(ch, items, results=results))
        assert result['applied'] is False
        assert (await store.channel(ch.id)).model_dump() == before


async def test_commit_status_four_states_are_read_only(store):
    ch, items, round_ = await delivered(store)
    binding = round_['binding_id']
    assert await store.commit_status(binding, 'missing') == {'state': 'not_found'}
    body = payload(ch, items)
    row = await store.ingest(f'agent:{binding}', 'pending', 'agent.commit_requested', body,
                             {'binding_id': binding, 'payload': body})
    assert await store.commit_status(binding, 'pending') == {'state': 'pending'}
    before = (await store.channel(ch.id)).model_dump()
    assert await store.commit_status('other-binding', 'pending') == {'state': 'not_found'}
    assert (await store.channel(ch.id)).model_dump() == before
    result = await store.apply_control(row.id)
    assert await store.commit_status(binding, 'pending') == {'state': 'applied', 'result': result}
    assert await store.commit_status(binding, 'missing') == {'state': 'not_found'}


async def test_competing_commit_changes_one_version(store):
    ch, items, round_ = await delivered(store)
    a, b = await asyncio.gather(
        control(store, round_, 'agent.commit_requested', payload(ch, items, progress='A')),
        control(store, round_, 'agent.commit_requested', payload(ch, items, progress='B')))
    assert sum(result['applied'] for _, result in (a, b)) == 1
    assert (await store.channel(ch.id)).version == 2


async def test_terminal_without_commit_retains_retryable_pending(store):
    ch, items, round_ = await delivered(store)
    await store.reconcile_round(ch.id, round_['wake_id'], 'completed')
    current = await store.channel(ch.id)
    assert set(current.runtime['pending']) == {x.id for x in items}
    assert all(x['delivery_attempt_count'] == 1 for x in current.runtime['pending'].values())


async def test_db_failure_rolls_back_channel_and_receipt(store, monkeypatch):
    ch, items, round_ = await delivered(store)
    body = payload(ch, items, progress='new')
    row = await store.ingest(f"agent:{round_['binding_id']}", 'rollback', 'agent.commit_requested', body,
                             {'binding_id': round_['binding_id'], 'payload': body})
    before = (await store.channel(ch.id)).model_dump()
    original = store._save
    async def fail_after_save(conn, channel, publish=False):
        await original(conn, channel, publish)
        raise RuntimeError('injected transaction failure')
    monkeypatch.setattr(store, '_save', fail_after_save)
    with pytest.raises(RuntimeError, match='injected'):
        await store.apply_control(row.id)
    assert (await store.channel(ch.id)).model_dump() == before
    assert await store.commit_status(round_['binding_id'], 'rollback') == {'state': 'pending'}
