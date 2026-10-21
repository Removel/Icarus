from datetime import UTC, datetime, timedelta
from uuid import uuid4

from apps.argus.test.factories import draft_factory, item_factory
from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.common.schemas.wake import WakeOutcome


async def test_explicit_no_action_is_processed_not_verified(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft_factory())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='hi'),), decision='wake', reason='test'))
    now = datetime.now(UTC)
    await repo.mark_accepted(wake.id, now=now, deadline_at=now + timedelta(seconds=60))
    outcome = WakeOutcome(wake_id=wake.id, item_ids=(item.id,), status='no_action', simulated=True, reason='not needed')
    await repo.finish_wake(wake.id, status='no_action', outcome=outcome)
    assert (await repo.get_item(item.id)).status == 'processed'
    assert (await repo.get_wake(wake.id)).outcome.status == 'no_action'


async def test_explicit_failure_is_terminal_not_requeued(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft_factory())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='hi'),), decision='wake', reason='test'))
    result = WakeOutcome(wake_id=wake.id, item_ids=(item.id,), status='failed', simulated=True, reason='cannot process')
    await repo.finish_wake(wake.id, status='failed', outcome=result)
    assert await repo.latest_open_items(item.chat_id, limit=20) == ()
    assert (await repo.get_item(item.id)).status == 'failed'
