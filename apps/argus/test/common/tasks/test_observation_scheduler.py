import asyncio
from pathlib import Path
from uuid import uuid4

from apps.argus.test.factories import Clock, draft_factory
from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.services.judgement_service import JudgementService
from apps.argus.src.common.services.rule_service import load_rule_set
from apps.argus.src.common.services.wake_service import WakeService
from apps.argus.src.common.clients.mock_agent_client import MockAgentClient
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService


async def make_flow(database):
    from apps.argus.src.common.tasks.observation_scheduler import ObservationScheduler
    clock = Clock()
    repo = ObservationRepository(database, boot_id=uuid4())
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


async def test_quiet_five_seconds_and_max_thirty_seconds(database):
    clock, repo, scheduler = await make_flow(database)
    item, _ = await repo.upsert_revision(draft_factory(observed_at=clock.now()))
    await tick(scheduler)
    assert (await repo.get_item(item.id)).status == 'new'
    clock.advance(5)
    await tick(scheduler)
    assert (await repo.get_item(item.id)).status == 'ignored'
    await scheduler.aclose()


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
