"""Real wakes hold a cross-boot scope until remote release, not mock terminal status."""

from uuid import uuid4

from apps.argus.test.common.repositories.test_observation_repository import draft
from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.wake import WakeReceipt


async def test_real_wake_does_not_mark_initial_delivered_on_accept_or_release_before_remote(database):
    from apps.argus.src.common.services.real_wake_service import RealWakeService

    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='question'),), decision='wake', reason='test'))
    stopped = {'phase': 'stopped', 'execution_stopped': True, 'release_complete': False}
    current = [None]
    class Agent:
        async def status(self, **kwargs): return current[0]
        async def submit(self, payload):
            current[0] = stopped
            return WakeReceipt(accepted=True)
        async def release(self, wake_id): return current[0]
    service = RealWakeService(repo, Agent())
    await service.advance(wake.id)
    assert (await repo.get_item(item.id)).status == 'new'
    assert (await repo.active_wake(item.chat_id)).id == wake.id
    current[0] = {**stopped, 'release_complete': True}
    await service.advance(wake.id)
    assert await repo.active_wake(item.chat_id) is None


async def test_real_scope_remains_owned_across_argus_boot_change(database):
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='question'),), decision='wake', reason='test'))
    class Agent:
        async def status(self, **kwargs): return None
        async def submit(self, payload): return WakeReceipt(accepted=True)
    await RealWakeService(repo, Agent()).advance(wake.id)
    fresh = ObservationRepository(database, boot_id=uuid4())
    assert (await fresh.active_wake(item.chat_id)).id == wake.id


async def test_real_busy_retries_original_wake_without_losing_scope(database):
    from datetime import UTC, datetime, timedelta
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.clients.gateway_client import GatewayBusinessError
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='question'),), decision='wake', reason='test'))
    submissions = []
    class Agent:
        async def status(self, **kwargs): return None
        async def submit(self, payload):
            submissions.append(payload.wake_id)
            if len(submissions) == 1: raise GatewayBusinessError('capacity_busy', 'full', data={'retryable': True})
            return WakeReceipt(accepted=True)
    now = [datetime.now(UTC)]
    service = RealWakeService(repo, Agent(), clock=lambda: now[0])
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'waiting'
    now[0] += timedelta(seconds=2)
    await service.advance(wake.id)
    assert submissions == [wake.id, wake.id]
    assert (await repo.get_wake(wake.id)).status == 'accepted'


async def test_definite_capacity_rejection_can_end_old_unsent_wake_after_restart(database):
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.clients.gateway_client import GatewayBusinessError
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='question'),), decision='wake', reason='test'))
    class Agent:
        async def status(self, **kwargs): return None
        async def submit(self, payload): raise GatewayBusinessError('capacity_busy','full',data={'retryable':True})
    await RealWakeService(repo,Agent()).advance(wake.id)
    fresh = ObservationRepository(database,boot_id=uuid4())
    await RealWakeService(fresh,Agent()).advance(wake.id)
    assert await fresh.active_wake(item.chat_id) is None
    assert (await fresh.get_wake(wake.id)).handoff['local_terminal'] is True
