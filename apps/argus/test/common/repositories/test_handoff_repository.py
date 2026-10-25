"""Real PG handoff distinguishes delivery, ownership and accepted reports."""

from uuid import uuid4

from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.test.common.repositories.test_observation_repository import draft


async def bound(database):
    from apps.argus.src.common.repositories.handoff_repository import HandoffRepository

    observations = ObservationRepository(database, boot_id=uuid4())
    first, _ = await observations.upsert_revision(draft('initial', message_id='initial'))
    wake = await observations.commit_judgement(first.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=first, excerpt='initial'),), decision='wake', reason='test'))
    handoff = HandoffRepository(database)
    binding = await handoff.bind(wake.id, {
        'execution_id': str(uuid4()), 'session_id': str(uuid4()), 'task_id': str(uuid4()),
        'scope': {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'},
        'origin_boot_id': str(wake.boot_id),
    })
    return observations, handoff, first, wake, binding['binding_id']


async def test_legacy_binding_replay_tolerates_explicit_v1_envelope(database):
    _, handoff, _, _, binding_id = await bound(database)
    original = await handoff.binding(binding_id)
    body = {key: original[key] for key in ('execution_id', 'session_id', 'task_id', 'scope', 'origin_boot_id')}
    result = await handoff.bind(original['wake_id'], {**body, 'protocol_version': 1})
    assert result['binding_id'] == binding_id and 'protocol_version' not in result


async def test_incidental_delivery_does_not_claim_and_finalize_preserves_new(database):
    observations, handoff, first, wake, binding = await bound(database)
    incidental, _ = await observations.upsert_revision(draft('incidental', message_id='incidental'))
    await handoff.mark_exposed(binding, [first.id, incidental.id])
    read = await observations.get_item(incidental.id)
    assert read.exposed_wake_id == wake.id
    assert read.wake_id is None and read.status == 'new'
    receipt = await handoff.report(binding, {'report_id': 'first', 'items': [
        {'observation_id': str(first.id), 'status': 'handled'}]})
    assert receipt['disposition'] == 'accepted' and receipt['report_cursor'] == 1
    await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'completed', 'report_cursor': 1})
    assert (await observations.get_item(first.id)).status == 'processed'
    assert (await observations.get_item(incidental.id)).wake_id is None
    assert (await observations.get_item(incidental.id)).status == 'new'


async def test_report_rejection_and_success_replay_are_durable(database):
    observations, handoff, first, wake, binding = await bound(database)
    await handoff.mark_exposed(binding, [first.id])
    good = {'report_id': 'report', 'items': [{'observation_id': str(first.id), 'status': 'handled'}]}
    accepted = await handoff.report(binding, good)
    assert await handoff.report(binding, good) == accepted
    rejected_input = {'report_id': 'conflict', 'items': [{'observation_id': str(first.id), 'status': 'no_action'}]}
    rejected = await handoff.report(binding, rejected_input)
    assert rejected['disposition'] == 'rejected' and rejected['report_cursor'] == 1
    assert await handoff.report(binding, rejected_input) == rejected
    assert (await observations.get_item(first.id)).status == 'processed'
    sealed = await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'failed', 'report_cursor': 1})
    assert sealed['initial_results'][0]['result'] == 'handled'
    assert (await observations.get_wake(wake.id)).status == 'resolved'


async def test_unreported_initial_is_unresolved_but_read_incidental_is_not(database):
    observations, handoff, first, wake, binding = await bound(database)
    second, _ = await observations.upsert_revision(draft('second', message_id='second'))
    await handoff.mark_exposed(binding, [first.id, second.id])
    sealed = await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'cancelled', 'report_cursor': 0})
    assert sealed['initial_results'] == [{'observation_id': str(first.id), 'result': 'unresolved'}]
    initial = await observations.get_item(first.id)
    assert initial.status == 'failed' and initial.status_reason == 'unresolved' and initial.wake_id == wake.id
    assert (await observations.get_item(second.id)).status == 'new'
    assert await handoff.finalize(binding, {'execution_stopped': True, 'exit_kind': 'cancelled', 'report_cursor': 0}) == sealed


async def test_bind_rejects_wrong_frozen_request_fingerprint(database):
    import pytest
    from apps.argus.src.common.repositories.handoff_repository import HandoffRepository, HandoffConflict
    observations = ObservationRepository(database, boot_id=uuid4())
    first, _ = await observations.upsert_revision(draft('initial', message_id='initial'))
    wake = await observations.commit_judgement(first.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=first, excerpt='initial'),), decision='wake', reason='test'))
    with pytest.raises(HandoffConflict, match='submission_conflict'):
        await HandoffRepository(database).bind(wake.id, {
            'execution_id': str(uuid4()), 'session_id': str(uuid4()), 'task_id': str(uuid4()),
            'scope': {'source': 'qq', 'account_id': '123', 'chat_kind': 'group', 'external_id': '456'},
            'origin_boot_id': str(wake.boot_id), 'request_fingerprint': '0' * 64,
        })


async def test_report_order_normalization_and_mixed_invalid_batch_are_atomic(database):
    observations, handoff, first, _, binding = await bound(database)
    second, _ = await observations.upsert_revision(draft('second', message_id='second'))
    await handoff.mark_exposed(binding, [first.id, second.id])
    values = [{'observation_id': str(first.id), 'status': 'handled'}, {'observation_id': str(second.id), 'status': 'no_action'}]
    first_receipt = await handoff.report(binding, {'report_id': 'ordered', 'items': values})
    assert await handoff.report(binding, {'report_id': 'ordered', 'items': list(reversed(values))}) == first_receipt


async def test_mixed_valid_and_unseen_report_updates_nothing(database):
    observations, handoff, first, _, binding = await bound(database)
    second, _ = await observations.upsert_revision(draft('unseen', message_id='unseen'))
    await handoff.mark_exposed(binding, [first.id])
    result = await handoff.report(binding, {'report_id': 'mixed', 'items': [
        {'observation_id': str(first.id), 'status': 'handled'}, {'observation_id': str(second.id), 'status': 'handled'}]})
    assert result['disposition'] == 'rejected' and result['report_cursor'] == 0
    assert (await observations.get_item(first.id)).status == 'delivered'
    assert (await observations.get_item(second.id)).status == 'new'
