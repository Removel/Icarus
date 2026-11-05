"""The selected Session is durable even when no task has been admitted."""
from uuid import uuid4

from apps.argus.test.argus_app.infrastructure.test_store import store


async def test_round_records_selection_before_admission(store):
    item = await store.ingest('qq:1:group:2', 'message', 'message.observed', {'text':'hello'})
    channel = await store.create_channel('title', 'goal', [item.source_key])
    await store.decide(channel.id, item.id, channel.version, {'passed': True, 'reason': 'needed'})
    round_ = await store.begin_round(channel.id)
    assert hasattr(store, 'set_round_session'), 'Session selection must be persisted separately'
    session = str(uuid4())
    await store.set_round_session(channel.id, round_['wake_id'], session)
    current = (await store.channel(channel.id)).runtime['round']
    assert current['session_id'] == session
    assert current['task_id'] is None
    await store.set_round_submission(channel.id, round_['wake_id'], 'not_admitted',
        attempt=1, retryable=True, error_code='runtime_load_failed')
    await store.set_round_submission(channel.id, round_['wake_id'], 'accepted', attempt=2)
    await store.set_round_submission(channel.id, round_['wake_id'], 'not_admitted', attempt=1, retryable=True)
    current = (await store.channel(channel.id)).runtime['round']
    assert current['submission']['admission_state'] == 'accepted'
    assert current['submission']['attempt'] == 2
    assert not current['submission']['retryable']
