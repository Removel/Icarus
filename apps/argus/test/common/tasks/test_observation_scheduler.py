import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from apps.argus.test.factories import Clock, draft_factory, item_factory
from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.services.judgement_service import JudgementService
from apps.argus.src.common.services.rule_service import load_rule_set
from apps.argus.src.common.services.wake_service import WakeService
from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService


async def make_flow(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    clock = Clock()
    repo = ObservationRepository(database, boot_id=uuid4(), clock=clock.now)
    surface = QQFactService()
    rules = load_rule_set(Path(__file__).resolve().parents[3] / 'rules/qq.yaml', known_facts=set(surface.known_facts))
    judgement = JudgementService(rules, surface, None)
    agent = MockAgentClient(completion_delay_seconds=1, clock=clock.now)
    wakes = WakeService(repo, agent, clock=clock.now)
    scheduler = ObservationScheduler(repo, judgement, wakes, clock=clock.now)
    return clock, repo, scheduler


async def tick(scheduler):
    await scheduler.scan_once()
    await asyncio.gather(*tuple(scheduler.workers.values()))


async def test_real_active_a_accepts_a2_notifies_only_a_and_queues_b(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.repositories.handoff_repository import HandoffRepository
    from apps.argus.src.common.schemas.wake import WakeReceipt
    from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    clock, repo, base = await make_flow(database)
    session = str(uuid4())
    submitted, notifications, remote = [], [], [None]
    handoff = HandoffRepository(database, boot_id=repo.boot_id)
    class Agent:
        async def status(self, **kwargs): return remote[0]
        async def sessions(self, scope, **kwargs):
            return {'protocol_version': 2, 'sessions': [] if not submitted else [
                {'session_id': session, 'summary': 'topic A', 'last_related_at': None, 'has_work': True}], 'next_cursor': None}
        async def submit(self, payload):
            submitted.append(payload)
            await handoff.bind(payload.wake_id, {'protocol_version': 2, 'session_id': session,
                'execution_id': str(uuid4()), 'task_id': str(uuid4()), 'origin_boot_id': str(repo.boot_id),
                'scope': {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'}})
            remote[0] = {'phase': 'running', 'execution_stopped': False, 'release_complete': False, 'session_id': session}
            return WakeReceipt(accepted=True)
        async def notify(self, wake_id, session_id):
            notifications.append((wake_id, session_id))
            return {'protocol_version': 2, 'accepted': True, 'merged': True}
    async def answer(state, questions):
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice='candidate_0' if state['items'][n]['text'].startswith('A') else f'new_{n}',
            confidence=1, probabilities={}) for n, name in enumerate(questions)})
    agent = Agent()
    wakes = RealWakeService(repo, agent, handoff=handoff, clock=clock.now)
    scheduler = ObservationScheduler(repo, base.judgement, wakes, routing=SessionRoutingService(JevClient(answer=answer)), clock=clock.now)
    first, _ = await repo.upsert_revision(draft_factory('A first', strong_signal=True, detail={'mentions_bot': True}))
    await scheduler.process_chat(first.chat_id)
    assert len(submitted) == 1 and submitted[0].protocol_version == 2
    a2, _ = await repo.upsert_revision(draft_factory('A again', strong_signal=True, detail={'mentions_bot': True}))
    b, _ = await repo.upsert_revision(draft_factory('B unrelated', strong_signal=True, detail={'mentions_bot': True}))
    await scheduler.process_chat(first.chat_id)
    assert (await repo.get_item(a2.id)).wake_id == submitted[0].wake_id
    assert notifications == [(submitted[0].wake_id, session)]
    waiting = await repo.waiting_wakes(first.chat_id)
    assert len(waiting) == 1 and waiting[0].id == (await repo.get_item(b.id)).wake_id
    assert waiting[0].payload.target_session_id is None and len(submitted) == 1
    binding = (await repo.get_wake(submitted[0].wake_id)).handoff['binding']['binding_id']
    await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 0})
    remote[0] = {**remote[0], 'release_complete': True}
    await scheduler.process_chat(first.chat_id)
    # A2 retained its target and is batched without another model classification.
    assert (await repo.get_item(a2.id)).detail['argus_session_id'] == session
    assert (await repo.get_item(a2.id)).status == 'new'
    assert any(str(wake.payload.target_session_id) == session for wake in await repo.waiting_wakes(first.chat_id))
    await scheduler.aclose()


@pytest.mark.parametrize('deleted', [False, True], ids=['edit', 'recall'])
async def test_stale_waiting_batch_returns_unaffected_assigned_work_after_active_release(database, deleted):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.schemas.judgement import JudgementOutcome
    from apps.argus.src.common.schemas.observation import SelectedItem
    from apps.argus.src.common.schemas.wake import WakeReceipt
    clock, repo, base = await make_flow(database)
    owner, _ = await repo.upsert_revision(draft_factory('A active'))
    active = await repo.commit_judgement(owner.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=owner, excerpt='A active'),), decision='wake', reason='owner'),
        protocol_version=2, target_session_id=None)
    await repo.prepare_submission(active.id)
    target = str(uuid4())
    first, _ = await repo.upsert_revision(draft_factory('B first', message_id='B-first'))
    tail, _ = await repo.upsert_revision(draft_factory('B unaffected', message_id='B-tail', detail={'keep': 'tail'}))
    waiting = await repo.commit_judgement(first.chat_id, JudgementOutcome(selected=(
        SelectedItem(item=first, excerpt='B first'), SelectedItem(item=tail, excerpt='B unaffected')),
        decision='wake', reason='same topic'), protocol_version=2, target_session_id=target)
    submitted = []
    class Agent:
        async def status(self, **kwargs):
            if 'wake_id' in kwargs:
                return {'phase': 'stopped', 'execution_stopped': True, 'release_complete': True}
            return None
        async def submit(self, payload):
            submitted.append(payload)
            return WakeReceipt(accepted=True)
    service = RealWakeService(repo, Agent(), clock=clock.now)
    await service.advance(waiting.id)
    assert not submitted and (await repo.get_wake(waiting.id)).status == 'waiting'
    await repo.upsert_revision(draft_factory('B edited', message_id='B-first', deleted=deleted,
        **({'content': ()} if deleted else {}), detail={'argus_session_id': target}))
    clock.advance(2)
    await service.advance(active.id)
    await service.advance(waiting.id)
    failed = await repo.get_wake(waiting.id)
    assert failed.handoff['local_terminal'] is True and failed.status == 'failed'
    assert failed.payload == waiting.payload
    assert failed.handoff['request_fingerprint'] == waiting.handoff['request_fingerprint']
    recovered = await repo.get_item(tail.id)
    assert recovered.wake_id is None and recovered.status == 'new'
    assert recovered.detail == {'keep': 'tail', 'argus_session_id': target}
    assert tail.id in {value.id for value in await repo.latest_open_items(tail.chat_id)}
    scheduler = ObservationScheduler(repo, base.judgement, service,
        routing=SessionRoutingService(None), clock=clock.now, quiet_seconds=0)
    try:
        await scheduler.process_chat(tail.chat_id)
        assert len(submitted) == 1 and submitted[0].wake_id != waiting.id
        assert str(submitted[0].target_session_id) == target
        assert tail.id in {value.item_id for value in submitted[0].items}
        assert first.id not in {value.item_id for value in submitted[0].items}
    finally:
        await scheduler.aclose()


@pytest.mark.parametrize('failure', ['business', 'transport', 'bug'])
@pytest.mark.parametrize('failed_page', [1, 2])
async def test_candidate_query_failure_does_not_starve_persisted_waiting_admission(database, failure, failed_page):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.schemas.judgement import JudgementOutcome
    from apps.argus.src.common.schemas.observation import SelectedItem
    from apps.argus.src.common.schemas.wake import WakeReceipt
    from apps.argus.src.common.clients.gateway_client import GatewayBusinessError, GatewayTransportError
    clock, repo, base = await make_flow(database)
    owner, _ = await repo.upsert_revision(draft_factory('A active'))
    active = await repo.commit_judgement(owner.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=owner, excerpt='A active'),), decision='wake', reason='owner'),
        protocol_version=2, target_session_id=None)
    await repo.prepare_submission(active.id)
    queued, _ = await repo.upsert_revision(draft_factory('B waiting'))
    waiting = await repo.commit_judgement(queued.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=queued, excerpt='B waiting'),), decision='wake', reason='queued'),
        protocol_version=2, target_session_id=None)
    pending, _ = await repo.upsert_revision(draft_factory('C new', strong_signal=True, detail={'mentions_bot': True}))
    submitted, pages = [], []
    class Agent:
        async def status(self, **kwargs):
            if 'wake_id' in kwargs:
                return {'phase': 'stopped', 'execution_stopped': True, 'release_complete': True}
            return None
        async def sessions(self, scope, **kwargs):
            pages.append(kwargs.get('cursor'))
            if len(pages) < failed_page:
                return {'protocol_version': 2, 'sessions': [], 'next_cursor': 'second'}
            if failure == 'business':
                raise GatewayBusinessError('unavailable', 'candidate pending check failed', data={'retryable': True})
            if failure == 'transport':
                raise GatewayTransportError('candidate query disconnected')
            raise TypeError('candidate query programming defect')
        async def submit(self, payload):
            submitted.append(payload)
            return WakeReceipt(accepted=True)
    scheduler = ObservationScheduler(repo, base.judgement, RealWakeService(repo, Agent(), clock=clock.now),
        routing=SessionRoutingService(None), clock=clock.now)
    try:
        if failure == 'bug':
            with pytest.raises(TypeError, match='programming defect'):
                await scheduler.process_chat(pending.chat_id)
            assert submitted == []
        else:
            await scheduler.process_chat(pending.chat_id)
            assert [payload.wake_id for payload in submitted] == [waiting.id]
            assert (await repo.get_wake(waiting.id)).status == 'accepted'
        assert (await repo.get_wake(active.id)).handoff['release_complete'] is True
        current = await repo.get_item(pending.id)
        assert current.status == 'new' and current.wake_id is None
        assert 'argus_session_id' not in current.detail
        assert len(await repo.list_wakes()) == 2
        assert pages == ([None] if failed_page == 1 else [None, 'second'])
    finally:
        await scheduler.aclose()


async def test_explicit_reply_outside_bounded_candidates_stays_pending(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    clock, repo, base = await make_flow(database)
    target = str(uuid4())
    previous, _ = await repo.upsert_revision(draft_factory('old A', message_id='old'))
    await repo.assign_to_session([previous.id], session_id=target)
    # Old history need not be executable pending for a reply to locate its association.
    from sqlalchemy import text
    async with database.begin() as db:
        await db.execute(text("UPDATE observed_items SET status='processed' WHERE id=:id"), {'id': previous.id})
    calls, submissions = [], []
    class Agent:
        async def status(self, **kwargs): return None
        async def sessions(self, scope, **kwargs):
            calls.append(kwargs)
            return {'protocol_version': 2, 'sessions': [{'session_id': str(uuid4()), 'summary': 'other',
                'last_related_at': None, 'has_work': False}], 'next_cursor': 'second' if len(calls) == 1 else None}
        async def submit(self, payload):
            submissions.append(payload)
            from apps.argus.src.common.schemas.wake import WakeReceipt
            return WakeReceipt(accepted=True)
    scheduler = ObservationScheduler(repo, base.judgement, RealWakeService(repo, Agent()),
        routing=SessionRoutingService(None), clock=clock.now)
    item, _ = await repo.upsert_revision(draft_factory('yes', strong_signal=True,
        detail={'mentions_bot': True, 'reply_to': 'old'}))
    await scheduler.process_chat(item.chat_id)
    assert (await repo.get_item(item.id)).wake_id is None and submissions == []
    assert len(calls) == 2 and calls[1]['cursor'] == 'second'
    await scheduler.aclose()


async def test_missing_reply_defers_only_itself_not_independent_direct_request(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.schemas.wake import WakeReceipt
    from typesafe_sdk import ChoiceAnswer, SystemOneResponse, Usage
    from apps.argus.src.common.clients.jev_client import JevClient
    from sqlalchemy import text
    clock, repo, base = await make_flow(database)
    target = str(uuid4())
    old, _ = await repo.upsert_revision(draft_factory('old', message_id='old'))
    await repo.assign_to_session([old.id], session_id=target)
    async with database.begin() as db:
        await db.execute(text("UPDATE observed_items SET status='processed' WHERE id=:id"), {'id': old.id})
    submitted = []
    class Agent:
        async def sessions(self, scope, **kwargs):
            return {'protocol_version': 2, 'sessions': [{'session_id': str(uuid4()), 'summary': 'other',
                'last_related_at': None, 'has_work': False}], 'next_cursor': None}
        async def status(self, **kwargs): return None
        async def submit(self, payload):
            submitted.append(payload)
            return WakeReceipt(accepted=True)
    async def answer(state, questions):
        return SystemOneResponse(model='fixture', usage=Usage(), answers={name: ChoiceAnswer(
            choice=f'new_{n}', confidence=1, probabilities={}) for n, name in enumerate(questions)})
    scheduler = ObservationScheduler(repo, base.judgement, RealWakeService(repo, Agent()),
        routing=SessionRoutingService(JevClient(answer=answer)), clock=clock.now)
    reply, _ = await repo.upsert_revision(draft_factory('yes', strong_signal=True,
        detail={'mentions_bot': True, 'reply_to': 'old'}))
    direct, _ = await repo.upsert_revision(draft_factory('independent', strong_signal=True, detail={'mentions_bot': True}))
    for _ in range(2):
        await scheduler.process_chat(direct.chat_id)
    assert submitted and len({payload.wake_id for payload in submitted}) == 1
    assert all(payload.items[0].item_id == direct.id for payload in submitted)
    assert (await repo.get_item(reply.id)).wake_id is None
    await scheduler.aclose()


async def test_deferred_reply_front_does_not_monopolize_bounded_selection(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.schemas.wake import WakeReceipt
    clock, repo, base = await make_flow(database)
    for n in range(21):
        await repo.upsert_revision(draft_factory('yes', message_id=f'deferred-{n}', strong_signal=True,
            detail={'mentions_bot': True, 'reply_to': 'old'}))
    await repo.routing_deferred([item.id for item in await repo.latest_open_items('qq:123:group:456', limit=30)],
        reason='reply_target_not_in_bounded_candidates')
    direct, _ = await repo.upsert_revision(draft_factory('independent', strong_signal=True, detail={'mentions_bot': True}))
    submitted = []
    class Agent:
        async def sessions(self, scope, **kwargs): return {'protocol_version': 2, 'sessions': [], 'next_cursor': None}
        async def status(self, **kwargs): return None
        async def submit(self, payload):
            submitted.append(payload)
            return WakeReceipt(accepted=True)
    scheduler = ObservationScheduler(repo, base.judgement, RealWakeService(repo, Agent()),
        routing=SessionRoutingService(None), clock=clock.now)
    await scheduler.process_chat(direct.chat_id)
    assert any(value.item_id == direct.id for payload in submitted for value in payload.items)
    await scheduler.aclose()


async def test_assigned_continuation_uses_shared_character_budget_and_keeps_overflow(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    from apps.argus.src.common.services.real_wake_service import RealWakeService
    from apps.argus.src.common.services.session_routing_service import SessionRoutingService
    from apps.argus.src.common.schemas.wake import WakeReceipt
    clock, repo, base = await make_flow(database)
    target = str(uuid4())
    values = [(await repo.upsert_revision(draft_factory('x'*15000, message_id=f'long-{n}', strong_signal=True)))[0]
        for n in range(2)]
    await repo.assign_to_session([value.id for value in values], session_id=target)
    submitted = []
    class Agent:
        async def status(self, **kwargs): return None
        async def submit(self, payload):
            submitted.append(payload)
            return WakeReceipt(accepted=True)
    scheduler = ObservationScheduler(repo, base.judgement, RealWakeService(repo, Agent()),
        routing=SessionRoutingService(None), clock=clock.now)
    await scheduler.process_chat(values[0].chat_id)
    assert len(submitted) == 1
    assert sum(len(item.excerpt) for item in submitted[0].items) <= base.judgement.max_chars
    overflow = await repo.get_item(values[1].id)
    assert overflow.wake_id is None and overflow.status == 'new' and overflow.detail['argus_session_id'] == target
    await scheduler.aclose()


async def test_quiet_five_seconds_and_max_thirty_seconds(database):
    clock, repo, scheduler = await make_flow(database)
    item, _ = await repo.upsert_revision(draft_factory(observed_at=clock.now()))
    await tick(scheduler)
    assert (await repo.get_item(item.id)).status == 'new'
    clock.advance(5)
    await tick(scheduler)
    assert (await repo.get_item(item.id)).status == 'ignored'
    await scheduler.aclose()


def test_merge_recent_prefers_database_and_adds_history_self():
    from datetime import UTC, datetime
    from apps.argus.src.common.tasks.observation_scheduler import merge_recent
    early = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    late = datetime(2026, 10, 25, 10, 1, tzinfo=UTC)
    database_item = item_factory('db', message_id='1', sent_at=early, observed_at=early)
    duplicate = draft_factory('history duplicate', message_id='1', sent_at=early, observed_at=early)
    self_message = draft_factory('Icarus reply', message_id='2', is_self=True,
                                 sender_name='Icarus', sent_at=late, observed_at=late)
    merged = merge_recent((database_item,), (duplicate, self_message), limit=10)
    assert [value.message_id for value in merged] == ['1', '2']
    assert merged[0] is database_item
    assert merged[1].is_self is True
    assert merge_recent((database_item,), (), limit=0) == ()


async def test_new_mention_waits_for_current_request_then_wakes_again(database):
    clock, repo, scheduler = await make_flow(database)
    first, _ = await repo.upsert_revision(draft_factory('help', observed_at=clock.now(),
        strong_signal=True, detail={'mentions_bot': True}))
    await tick(scheduler)
    wake1 = await repo.active_wake(first.chat_id)
    assert wake1.status == 'accepted'
    second, _ = await repo.upsert_revision(draft_factory('more help', observed_at=clock.now(),
        strong_signal=True, detail={'mentions_bot': True}))
    await tick(scheduler)
    assert (await repo.active_wake(first.chat_id)).id == wake1.id
    assert (await repo.get_item(second.id)).wake_id is None
    clock.advance(1)
    await tick(scheduler)
    assert (await repo.get_wake(wake1.id)).status == 'resolved'
    await tick(scheduler)
    wake2 = await repo.active_wake(first.chat_id)
    assert wake2 is not None and wake2.id != wake1.id
    await scheduler.aclose()


async def test_max_debounce_and_newest_beyond_budget(database):
    clock, repo, scheduler = await make_flow(database)
    for n in range(21):
        await repo.upsert_revision(draft_factory('hi', observed_at=clock.now()))
    clock.advance(4)
    await repo.upsert_revision(draft_factory('newest', observed_at=clock.now()))
    clock.advance(2)
    await tick(scheduler)
    assert len(await repo.latest_open_items('qq:123:group:456', limit=100)) == 22
    clock.advance(24)
    await tick(scheduler)
    assert len(await repo.latest_open_items('qq:123:group:456', limit=100)) == 2
    await scheduler.aclose()


async def test_new_chat_after_restart_ignores_old_accepted(database):
    clock, old, scheduler = await make_flow(database)
    await old.upsert_revision(draft_factory('old', observed_at=clock.now(),
        strong_signal=True, detail={'mentions_bot': True}))
    await tick(scheduler)
    assert (await old.active_wake('qq:123:group:456')).status == 'accepted'
    await scheduler.aclose()
    _, fresh, next_scheduler = await make_flow(database)
    await fresh.upsert_revision(draft_factory('new', observed_at=clock.now(),
        strong_signal=True, detail={'mentions_bot': True}))
    await tick(next_scheduler)
    assert (await fresh.active_wake('qq:123:group:456')).status == 'accepted'
    assert len(await fresh.list_wakes()) == 2
    await next_scheduler.aclose()


async def test_revision_changed_while_judge_waits_is_not_reserved(database):
    clock, repo, scheduler = await make_flow(database)
    entered, release = asyncio.Event(), asyncio.Event()
    original = scheduler.judgement
    class Delayed:
        async def judge(self, items, **kwargs):
            entered.set()
            await release.wait()
            return await original.judge(items, **kwargs)
    scheduler.judgement = Delayed()
    first, _ = await repo.upsert_revision(draft_factory('help', message_id='123', observed_at=clock.now(),
        strong_signal=True, detail={'mentions_bot': True}))
    await scheduler.scan_once()
    await asyncio.wait_for(entered.wait(), 1)
    await repo.upsert_revision(draft_factory('', message_id='123', deleted=True, content=(), observed_at=clock.now()))
    release.set()
    await asyncio.gather(*scheduler.workers.values())
    assert (await repo.get_item(first.id)).wake_id is None
    assert await repo.active_wake(first.chat_id) is None
    await scheduler.aclose()


async def test_one_chat_blocked_does_not_block_other(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    clock, repo, base = await make_flow(database)
    blocked, released = asyncio.Event(), asyncio.Event()
    original = base.judgement
    class Slow:
        async def judge(self, items, **kwargs):
            if items[0].chat_id == 'a':
                blocked.set()
                await released.wait()
            return await original.judge(items, **kwargs)
    base.judgement = Slow()
    for chat in ('a', 'b'):
        await repo.upsert_revision(draft_factory('help', chat_id=chat, observed_at=clock.now(),
            strong_signal=True, detail={'mentions_bot': True}))
    await base.scan_once()
    await asyncio.wait_for(blocked.wait(), 1)
    await asyncio.wait_for(base.workers['b'], 1)
    assert (await repo.active_wake('b')).status == 'accepted'
    assert await repo.active_wake('a') is None
    released.set()
    await base.aclose()
