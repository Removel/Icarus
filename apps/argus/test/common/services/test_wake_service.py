from datetime import timedelta
from uuid import uuid4

from apps.argus.test.factories import Clock, draft_factory
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.wake import WakeOutcome, WakeReceipt
from apps.argus.src.common.repositories.observation_repository import ObservationRepository


async def make_wake(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft_factory())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='hello'),), decision='wake', reason='test'))
    return repo, item, wake


async def test_acceptance_is_not_completion(database):
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    clock = Clock()
    agent = MockAgentClient(completion_delay_seconds=0.05, clock=clock.now)
    service = WakeService(repo, agent, clock=clock.now)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'accepted'
    assert (await repo.get_item(item.id)).status == 'delivered'
    clock.advance(.05)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'resolved'
    assert (await repo.get_item(item.id)).status == 'processed'
    assert await repo.active_wake(item.chat_id) is None
    assert agent._pending == {}


async def test_unknown_transport_retries_same_wake_and_exhausts(database):
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    clock, ids = Clock(), []
    class Offline:
        async def submit(self, payload):
            ids.append(payload.wake_id)
            raise ConnectionError('synthetic')
    service = WakeService(repo, Offline(), clock=clock.now)
    for seconds in (0, 1, 2):
        clock.advance(seconds)
        await service.advance(wake.id)
    assert ids == [wake.id] * 3
    assert (await repo.get_wake(wake.id)).status == 'failed'
    assert (await repo.get_item(item.id)).status == 'failed'


async def test_invalid_outcome_does_not_complete_foreign_item(database):
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    class Wrong:
        async def submit(self, payload):
            return WakeReceipt(accepted=True)
        async def get_outcome(self, wake_id):
            return WakeOutcome(wake_id=wake_id, item_ids=(uuid4(),), status='resolved', simulated=True, reason='bad')
    service = WakeService(repo, Wrong())
    await service.advance(wake.id)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).error_type == 'invalid_outcome'
    assert (await repo.get_item(item.id)).status == 'failed'


async def test_response_lost_after_acceptance_reuses_frozen_request(database):
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    clock = Clock()
    real = MockAgentClient(completion_delay_seconds=.05, clock=clock.now)
    class LostFirstResponse:
        def __init__(self):
            self.attempts = 0
        async def submit(self, payload):
            self.attempts += 1
            receipt = await real.submit(payload)
            if self.attempts == 1:
                raise TimeoutError('response lost after processor accepted')
            return receipt
        async def get_outcome(self, wake_id):
            return await real.get_outcome(wake_id)
    agent = LostFirstResponse()
    service = WakeService(repo, agent, clock=clock.now)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'waiting'
    clock.advance(1)
    await service.advance(wake.id)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'resolved'
    assert len(await repo.list_wakes()) == 1
    assert (await repo.get_item(item.id)).wake_id == wake.id


async def test_recall_after_reservation_before_first_submit_is_not_sent(database):
    from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    await repo.upsert_revision(draft_factory('', message_id=item.message_id, deleted=True, content=()))
    agent = MockAgentClient()
    await WakeService(repo, agent).advance(wake.id)
    assert (await repo.get_wake(wake.id)).status == 'failed'
    assert (await repo.get_wake(wake.id)).error_type == 'stale_before_submission'
    assert await agent.get_outcome(wake.id) is None
    assert agent._pending == {}


async def test_poll_errors_do_not_extend_deadline(database):
    from apps.argus.src.common.services.wake_service import WakeService
    repo, item, wake = await make_wake(database)
    clock = Clock()
    class BrokenPoll:
        async def submit(self, payload):
            return WakeReceipt(accepted=True)
        async def get_outcome(self, wake_id):
            raise ConnectionError('synthetic')
    service = WakeService(repo, BrokenPoll(), clock=clock.now)
    await service.advance(wake.id)
    await service.advance(wake.id)
    clock.advance(60)
    await service.advance(wake.id)
    assert (await repo.get_wake(wake.id)).error_type == 'outcome_timeout'
