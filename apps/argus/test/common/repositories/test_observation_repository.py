import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.argus.src.common.schemas.observation import ContentPart, ObservedItemDraft
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.wake import WakeOutcome
from apps.argus.src.common.repositories.observation_repository import ObservationRepository, StaleSelection


def draft(text='hello', *, message_id='789', deleted=False):
    now = datetime.now(UTC)
    return ObservedItemDraft(source='qq', chat_id='qq:123:group:456', message_id=message_id,
                            deleted=deleted, sender_id='777', sender_name='peer', sent_at=now,
                            observed_at=now, content=() if deleted else (ContentPart(kind='text', value=text),),
                            locator={'chat_kind': 'group'})


async def test_concurrent_same_message_inserts_once(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    results = await asyncio.gather(*(repo.upsert_revision(draft()) for _ in range(20)))
    assert len({item.id for item, _ in results}) == 1
    assert sum(inserted for _, inserted in results) == 1


async def test_edit_replay_and_recall_cannot_revert(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    first, _ = await repo.upsert_revision(draft('A'))
    changed, _ = await repo.upsert_revision(draft('B'))
    replayed, inserted = await repo.upsert_revision(draft('A'))
    assert changed.revision == 2 and replayed.id == first.id and not inserted
    recalled, _ = await repo.upsert_revision(draft(deleted=True))
    revived, inserted = await repo.upsert_revision(draft('C'))
    assert revived.id == recalled.id and not inserted
    assert await repo.latest_open_items(first.chat_id, limit=20) == ()


async def test_old_boot_does_not_block_new_work(database):
    old = ObservationRepository(database, boot_id=uuid4())
    first, _ = await old.upsert_revision(draft())
    fresh = ObservationRepository(database, boot_id=uuid4())
    assert await fresh.latest_open_items(first.chat_id, limit=20) == ()
    repeated, inserted = await fresh.upsert_revision(draft())
    assert not inserted and repeated.id == first.id
    await fresh.upsert_revision(draft('new', message_id='790'))
    assert len(await fresh.latest_open_items(first.chat_id, limit=20)) == 1


async def test_stale_selection_is_not_delivered(database):
    from apps.argus.src.common.schemas.observation import SelectedItem
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    await repo.upsert_revision(draft(deleted=True))
    choice = JudgementOutcome(selected=(SelectedItem(item=item, excerpt='hello'),),
                              decision='wake', reason='test')
    with pytest.raises(StaleSelection):
        await repo.commit_judgement(item.chat_id, choice)
    assert await repo.active_wake(item.chat_id) is None


async def test_same_chat_concurrent_wake_creation_has_one_winner(database):
    from apps.argus.src.common.schemas.observation import SelectedItem
    from apps.argus.src.common.repositories.observation_repository import ActiveWakeExists
    repo = ObservationRepository(database, boot_id=uuid4())
    first, _ = await repo.upsert_revision(draft(message_id='1'))
    second, _ = await repo.upsert_revision(draft(message_id='2'))
    def choice(item):
        return JudgementOutcome(selected=(SelectedItem(item=item, excerpt='hello'),), decision='wake', reason='test')
    results = await asyncio.gather(repo.commit_judgement(first.chat_id, choice(first)),
                                   repo.commit_judgement(second.chat_id, choice(second)), return_exceptions=True)
    assert sum(isinstance(result, ActiveWakeExists) for result in results) == 1
    assert len(await repo.list_wakes()) == 1
    assert len(await repo.latest_open_items(first.chat_id, limit=20)) == 1


async def test_recall_before_message_preserves_tombstone(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    tombstone, _ = await repo.upsert_revision(draft(deleted=True))
    late, inserted = await repo.upsert_revision(draft())
    assert late.id == tombstone.id and not inserted
    assert await repo.latest_open_items(tombstone.chat_id, limit=20) == ()


async def test_finish_is_idempotent_and_releases_chat(database):
    from apps.argus.src.common.schemas.observation import SelectedItem
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='hello'),), decision='wake', reason='test'))
    now = datetime.now(UTC)
    await repo.begin_attempt(wake.id, now=now)
    await repo.mark_accepted(wake.id, now=now, deadline_at=now + timedelta(seconds=60))
    assert (await repo.get_item(item.id)).status == 'delivered'
    outcome = WakeOutcome(wake_id=wake.id, item_ids=(item.id,), status='resolved', simulated=True, reason='mock')
    for _ in range(2):
        await repo.finish_wake(wake.id, status='resolved', outcome=outcome)
    assert (await repo.get_item(item.id)).status == 'processed'
    assert await repo.active_wake(item.chat_id) is None
