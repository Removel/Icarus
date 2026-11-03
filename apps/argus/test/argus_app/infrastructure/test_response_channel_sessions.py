"""Progress and execution selection change together; no duplicate business state."""
from sqlalchemy import text

from apps.argus.test.argus_app.infrastructure.test_store import control, store
from apps.argus.test.argus_app.infrastructure.test_commit import delivered, payload


async def test_commit_updates_progress_and_next_session_atomically(store):
    channel, items, round_ = await delivered(store)
    assert round_.get('session_key'), 'round must freeze an explicit Session selection'
    old = round_['session_key']
    row, result = await control(store, round_, 'agent.commit_requested',
        payload(channel, items, progress='阶段一已完成，下一会话继续', next_session='new'))
    assert result.get('committed') is True
    current = await store.channel(channel.id)
    assert current.progress == '阶段一已完成，下一会话继续'
    assert current.lifecycle == 'active'
    new = current.runtime['session_key']
    assert new != old
    assert await store.apply_control(row.id) == result
    assert (await store.channel(channel.id)).runtime['session_key'] == new
    assert 'state' not in current.model_dump()
    async with store.engine.connect() as connection:
        columns = (await connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='channels' AND table_schema=current_schema()"))).scalars().all()
    assert 'state' not in columns


async def test_failed_next_selection_commit_preserves_progress_key_and_round(store):
    channel, items, round_ = await delivered(store)
    before = (await store.channel(channel.id)).model_dump()
    _, result = await control(store, round_, 'agent.commit_requested',
        payload(channel, items, progress='must not apply', next_session='new', expected_version=999))
    assert result['applied'] is False
    assert (await store.channel(channel.id)).model_dump() == before


async def test_reuse_keeps_current_selection_and_omitted_progress(store):
    channel, items, round_ = await delivered(store)
    assert round_.get('session_key')
    _, result = await control(store, round_, 'agent.commit_requested', payload(channel, items, next_session='reuse'))
    assert result.get('committed') is True
    current = await store.channel(channel.id)
    assert current.runtime['session_key'] == round_['session_key']
    assert current.progress == channel.progress
