"""Schema3 and semantic ownership invariants on disposable PostgreSQL only."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from apps.argus.src.common.repositories.observation_repository import ObservationRepository, StaleSelection
from apps.argus.src.common.repositories.handoff_repository import HandoffRepository, HandoffConflict
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.test.common.repositories.test_observation_repository import draft

SCOPE = {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'}


def choice(item):
    return JudgementOutcome(selected=(SelectedItem(item=item, excerpt='initial'),), decision='wake', reason='test')


async def semantic_bound(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    first, _ = await repo.upsert_revision(draft('initial', message_id='initial'))
    target = str(uuid4())
    wake = await repo.commit_judgement(first.chat_id, choice(first), protocol_version=2, target_session_id=target)
    handoff = HandoffRepository(database, boot_id=repo.boot_id)
    binding = await handoff.bind(wake.id, {'protocol_version': 2, 'execution_id': str(uuid4()),
        'session_id': target, 'task_id': str(uuid4()), 'scope': SCOPE, 'origin_boot_id': str(repo.boot_id)})
    return repo, handoff, first, wake, binding['binding_id'], target


async def test_schema3_has_same_tables_and_waiting_is_not_an_execution_owner(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    items = [(await repo.upsert_revision(draft(message_id=str(n))))[0] for n in range(2)]
    wakes = [await repo.commit_judgement(item.chat_id, choice(item), protocol_version=2,
        target_session_id=str(uuid4())) for item in items]
    assert await repo.active_wake(items[0].chat_id) is None
    assert {wake.id for wake in await repo.waiting_wakes(items[0].chat_id)} == {wake.id for wake in wakes}
    results = await asyncio.gather(*(repo.prepare_submission(wake.id) for wake in wakes), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    async with database.session() as db:
        assert await db.scalar(text('SELECT version FROM schema_version')) == 3
        assert set((await db.execute(text('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'))).scalars()) == {
            'schema_version', 'observed_items', 'wake_requests'}


@pytest.mark.parametrize('state', ['unsent', 'possible_submission', 'old_boot'])
async def test_stale_batch_release_preserves_observed_rows_and_possible_submission_identity(database, state):
    from apps.argus.src.common.models.observation import ObservationRow
    repo = ObservationRepository(database, boot_id=uuid4())
    items = [(await repo.upsert_revision(draft(message_id=str(n))))[0] for n in range(3)]
    target = str(uuid4())
    wake = await repo.commit_judgement(items[0].chat_id, JudgementOutcome(
        selected=tuple(SelectedItem(item=item, excerpt='initial') for item in items), decision='wake', reason='test'),
        protocol_version=2, target_session_id=target)
    async with database.begin() as db:
        (await db.get(ObservationRow, items[1].id)).exposed_wake_id = wake.id
        (await db.get(ObservationRow, items[2].id)).status = 'processed'
    if state == 'possible_submission':
        await repo.prepare_submission(wake.id)
    caller = ObservationRepository(database, boot_id=uuid4()) if state == 'old_boot' else repo
    await caller.fail_stale_submission(wake.id)
    current = await repo.get_wake(wake.id)
    assert current.payload == wake.payload
    assert current.handoff['request_fingerprint'] == wake.handoff['request_fingerprint']
    assert (await repo.get_item(items[1].id)).wake_id == wake.id
    assert (await repo.get_item(items[2].id)).wake_id == wake.id
    untouched = await repo.get_item(items[0].id)
    assert untouched.detail['argus_session_id'] == target and untouched.status == 'new'
    if state == 'unsent':
        assert untouched.wake_id is None and current.handoff['local_terminal'] is True
        assert current.status == 'failed'
    else:
        assert untouched.wake_id == wake.id and not current.handoff.get('local_terminal')
        assert current.handoff.get('submission_may_have_run', False) == (state == 'possible_submission')


async def test_attach_is_atomic_idempotent_and_frozen_payload_is_unchanged(database):
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    extra, _ = await repo.upsert_revision(draft('extra', message_id='extra').model_copy(update={'detail': {'retained': True}}))
    before = await repo.get_wake(wake.id)
    result = await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id])
    assert result['attached_ids'] == [str(extra.id)]
    assert await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id]) == result
    after = await repo.get_wake(wake.id)
    assert before.payload == after.payload
    assert before.handoff['request_fingerprint'] == after.handoff['request_fingerprint']
    owned = await repo.get_item(extra.id)
    assert owned.wake_id == wake.id and owned.detail == {'retained': True, 'argus_session_id': target}
    with pytest.raises(HandoffConflict):
        await handoff.attach(wake.id, session_id=str(uuid4()), observation_ids=[extra.id])


async def test_assign_preserves_detail_and_rejects_superseded_or_claimed_items(database):
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    item, _ = await repo.upsert_revision(draft('extra', message_id='extra').model_copy(update={'detail': {'keep': 1}}))
    await repo.assign_to_session([item.id], session_id=target)
    assert (await repo.get_item(item.id)).detail == {'keep': 1, 'argus_session_id': target}
    await repo.upsert_revision(draft('edited', message_id='extra'))
    with pytest.raises((StaleSelection, HandoffConflict)):
        await handoff.attach(wake.id, session_id=target, observation_ids=[item.id])


async def test_waiting_merge_extends_only_never_submitted_payload(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    first, _ = await repo.upsert_revision(draft('first', message_id='first'))
    wake = await repo.commit_judgement(first.chat_id, choice(first), protocol_version=2, target_session_id=None)
    extra, _ = await repo.upsert_revision(draft('extra', message_id='extra'))
    merged = await repo.commit_judgement(extra.chat_id, choice(extra), protocol_version=2,
        target_session_id=None, target_waiting_wake_id=wake.id)
    assert merged.id == wake.id and len(merged.payload.items) == 2
    await repo.prepare_submission(wake.id)
    third, _ = await repo.upsert_revision(draft('third', message_id='third'))
    with pytest.raises((StaleSelection, HandoffConflict)):
        await repo.commit_judgement(third.chat_id, choice(third), protocol_version=2,
            target_session_id=None, target_waiting_wake_id=wake.id)


async def test_finalize_detaches_appended_pending_and_closed_attach_keeps_association(database):
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    extra, _ = await repo.upsert_revision(draft('extra', message_id='extra'))
    await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id])
    await handoff.mark_exposed(binding, [extra.id])
    result = await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 0})
    assert result['initial_results'] == [{'observation_id': str(first.id), 'result': 'unresolved'}]
    owned = await repo.get_item(extra.id)
    assert owned.wake_id is None and owned.status == 'new' and owned.exposed_wake_id is None
    assert owned.detail['argus_session_id'] == target
    assert extra.id in {item.id for item in await repo.latest_open_items(extra.chat_id)}
    with pytest.raises(HandoffConflict, match='execution_closed'):
        await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id])


async def test_v2_available_wait_pending_first_and_deliver_are_distinct(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    service = HandoffService(handoff)
    unrelated, _ = await repo.upsert_revision(draft('B', message_id='B'))
    extra, _ = await repo.upsert_revision(draft('extra', message_id='extra'))
    await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id])
    for _ in range(3):
        available = await service.available(binding)
        assert available['pending_count'] == 2 and set(available) == {'protocol_version', 'pending_count', 'watermark'}
    page = await service.wait(binding, timeout_seconds=0)
    assert {item['observation_id'] for item in page['items']} == {str(first.id), str(extra.id)}
    assert all(item['role'] == 'work' for item in page['items'])
    assert (await service.available(binding))['pending_count'] == 2
    assert (await repo.get_item(first.id)).exposed_wake_id is None
    await service.deliver(binding, [first.id])
    assert (await service.available(binding))['pending_count'] == 1
    retry = await service.wait(binding, cursor=page['next_cursor'], timeout_seconds=0)
    assert [item['observation_id'] for item in retry['items']] == [str(extra.id)]
    await service.read(binding, unrelated.id)
    denied = await service.report(binding, {'report_id': 'B', 'items': [
        {'observation_id': str(unrelated.id), 'status': 'handled'}]})
    assert denied['disposition'] == 'rejected'
    assert (await service.session_pending(target, SCOPE))['pending_count'] == 2


async def test_session_pending_counts_waiting_and_unreported_delivered_work(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    service = HandoffService(handoff)
    await service.deliver(binding, [first.id])
    assert (await service.available(binding))['pending_count'] == 0
    assert (await service.session_pending(target, SCOPE))['pending_count'] == 1
    second, _ = await repo.upsert_revision(draft('queued', message_id='queued'))
    await repo.commit_judgement(second.chat_id, choice(second), protocol_version=2, target_session_id=target)
    assert (await service.session_pending(target, SCOPE))['pending_count'] == 2


async def test_attach_race_finalize_never_loses_pending_association(database):
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    extra, _ = await repo.upsert_revision(draft('extra', message_id='racing'))
    await repo.assign_to_session([extra.id], session_id=target)
    outcomes = await asyncio.gather(handoff.attach(wake.id, session_id=target, observation_ids=[extra.id]),
        handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 0}),
        return_exceptions=True)
    assert not isinstance(outcomes[1], Exception)
    owned = await repo.get_item(extra.id)
    assert owned.wake_id is None and owned.status == 'new' and owned.detail['argus_session_id'] == target


async def seed_pending(database, first, wake, target, *, count, text='pending', superseded=False):
    from apps.argus.src.common.models.observation import ObservationRow
    from datetime import timedelta
    values = []
    for n in range(count):
        value = {**first.model_dump(), 'id': uuid4(), 'message_id': f'seed-{n}',
            'fingerprint': f'seed-{n}', 'content': [{'kind': 'text', 'value': text}],
            'observed_at': first.observed_at + timedelta(microseconds=n+1),
            'wake_id': wake.id, 'detail': {'argus_session_id': target}}
        values.append(value)
        if superseded:
            values.append({**value, 'id': uuid4(), 'revision': 2, 'fingerprint': f'edited-{n}', 'status': 'processed'})
    async with database.begin() as db:
        await db.execute(ObservationRow.__table__.insert(), values)


async def test_available_exactly_counts_more_than_repository_window(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    _, handoff, first, wake, binding, target = await semantic_bound(database)
    await seed_pending(database, first, wake, target, count=501)
    assert (await HandoffService(handoff).available(binding))['pending_count'] == 502


async def test_pending_sql_excludes_superseded_front_before_limiting(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    await handoff.mark_exposed(binding, [first.id])
    await seed_pending(database, first, wake, target, count=501, superseded=True)
    tail, _ = await repo.upsert_revision(draft('tail', message_id='tail'))
    await handoff.attach(wake.id, session_id=target, observation_ids=[tail.id])
    page = await HandoffService(handoff).wait(binding, timeout_seconds=0)
    assert [value['observation_id'] for value in page['items']] == [str(tail.id)]


async def test_policy_rejected_window_has_bounded_continuation_to_safe_tail(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.src.common.services.observation_access_policy import ObservationAccessPolicy
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    await handoff.mark_exposed(binding, [first.id])
    await seed_pending(database, first, wake, target, count=501, text='secret')
    tail, _ = await repo.upsert_revision(draft('safe', message_id='tail'))
    await handoff.attach(wake.id, session_id=target, observation_ids=[tail.id])
    service = HandoffService(handoff, policy=ObservationAccessPolicy(['secret']))
    assert (await service.available(binding))['pending_count'] == 1
    page = await service.wait(binding, timeout_seconds=0)
    assert page['items'] == [] and page['has_more'] is True
    next_page = await service.wait(binding, cursor=page['next_cursor'], timeout_seconds=0)
    assert [value['observation_id'] for value in next_page['items']] == [str(tail.id)]
    assert (await service.session_pending(target, SCOPE))['pending_count'] == 2


async def test_two_repositories_cannot_overwrite_competing_session_assignment(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft('racing'))
    entered, release = asyncio.Event(), asyncio.Event()
    class DelayedRepository(ObservationRepository):
        async def lock_scope(self, db, source, chat_id):
            entered.set()
            await release.wait()
            await super().lock_scope(db, source, chat_id)
    delayed = DelayedRepository(database, boot_id=repo.boot_id)
    first_target, second_target = str(uuid4()), str(uuid4())
    losing = asyncio.create_task(delayed.assign_to_session([item.id], session_id=second_target))
    await asyncio.wait_for(entered.wait(), 1)
    await repo.assign_to_session([item.id], session_id=first_target)
    release.set()
    outcome = await asyncio.gather(losing, return_exceptions=True)
    assert isinstance(outcome[0], StaleSelection)
    assert (await repo.get_item(item.id)).detail['argus_session_id'] == first_target


async def test_session_pending_preserves_waiting_but_excludes_orphan_old_boot(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    repo, handoff, first, wake, binding, target = await semantic_bound(database)
    extra, _ = await repo.upsert_revision(draft('extra', message_id='extra'))
    await handoff.attach(wake.id, session_id=target, observation_ids=[extra.id])
    fresh = HandoffRepository(database, boot_id=uuid4())
    assert (await HandoffService(fresh).session_pending(target, SCOPE))['pending_count'] == 2
    await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 0})
    assert (await HandoffService(fresh).session_pending(target, SCOPE))['pending_count'] == 0
    assert (await HandoffService(handoff).session_pending(target, SCOPE))['pending_count'] == 1
