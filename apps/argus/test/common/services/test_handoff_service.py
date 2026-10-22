"""Bounded read/longpoll retain pending work and separate boot generations."""

from uuid import uuid4

import pytest

from apps.argus.test.common.repositories.test_handoff_repository import bound
from apps.argus.test.common.repositories.test_observation_repository import draft


async def test_read_chunks_reconstruct_the_same_observation(database):
    from apps.argus.src.common.services.handoff_service import HandoffService

    observations, repository, _, _, binding = await bound(database)
    text = 'a' * 20000 + 'tail' * 1250
    item, _ = await observations.upsert_revision(draft(text, message_id='long'))
    service = HandoffService(repository)
    first = await service.read(binding, item.id)
    second = await service.read(binding, item.id, text_offset=first['next_text_offset'])
    assert first['text'] + second['text'] == text
    assert first['next_text_offset'] == 20000 and second['next_text_offset'] is None
    assert first['item']['role'] == 'work' and first['item']['origin'] == 'incidental'
    assert (await observations.get_item(item.id)).wake_id is None


async def test_first_wait_uses_initial_watermark_and_new_boot_is_context(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.src.common.repositories.observation_repository import ObservationRepository

    observations, repository, _, _, binding = await bound(database)
    early, _ = await observations.upsert_revision(draft('early reply', message_id='early'))
    service = HandoffService(repository)
    page = await service.wait(binding, timeout_seconds=0)
    assert [item['observation_id'] for item in page['items']] == [str(early.id)]
    fresh = ObservationRepository(database, boot_id=uuid4())
    new, _ = await fresh.upsert_revision(draft('after restart', message_id='new-boot'))
    next_page = await service.wait(binding, cursor=page['next_cursor'], timeout_seconds=0)
    assert next_page['items'][0]['observation_id'] == str(new.id)
    assert next_page['items'][0]['role'] == 'context'
    assert (await fresh.get_item(new.id)).status == 'new'


async def test_sensitive_policy_changed_after_delivery_rejects_report_durably(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.src.common.services.observation_access_policy import ObservationAccessPolicy

    observations, repository, first, _, binding = await bound(database)
    await repository.mark_exposed(binding, [first.id])
    service = HandoffService(repository, policy=ObservationAccessPolicy(['initial']))
    report = {'report_id': 'restricted', 'items': [{'observation_id': str(first.id), 'status': 'handled'}]}
    receipt = await service.report(binding, report)
    assert receipt['disposition'] == 'rejected' and receipt['code'] == 'content_restricted'
    assert receipt['report_cursor'] == 0
    assert await repository.get_report(binding, 'restricted') == receipt
    assert (await observations.get_item(first.id)).status != 'processed'


async def test_wait_notification_race_and_cancel_cleanup(database):
    import asyncio
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.src.common.services.observation_notifier import ObservationNotifier
    observations, repository, _, _, binding = await bound(database)
    notifier = ObservationNotifier()
    service = HandoffService(repository, notifier=notifier)
    waiting = asyncio.create_task(service.wait(binding, timeout_seconds=1))
    async with asyncio.timeout(1):
        while not notifier._waiters: await asyncio.sleep(0)
    item, _ = await observations.upsert_revision(draft('answer', message_id='notification-answer'))
    notifier.notify(item.chat_id)
    page = await waiting
    assert page['items'][0]['observation_id'] == str(item.id)
    pending = asyncio.create_task(service.wait(binding, cursor=page['next_cursor'], timeout_seconds=30))
    async with asyncio.timeout(1):
        while not notifier._waiters: await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError): await pending
    assert notifier._waiters == {}


async def test_sensitive_tail_is_never_exposed_and_filtered_page_can_advance(database):
    from apps.argus.src.common.services.handoff_service import HandoffService
    from apps.argus.src.common.services.observation_access_policy import ObservationAccessPolicy
    from apps.argus.src.common.repositories.handoff_repository import HandoffConflict

    observations, repository, _, _, binding = await bound(database)
    item, _ = await observations.upsert_revision(draft('a' * 25000 + 'secret', message_id='restricted'))
    service = HandoffService(repository, policy=ObservationAccessPolicy(['secret']))
    with pytest.raises(HandoffConflict, match='content_restricted'):
        await service.read(binding, item.id)
    page = await service.wait(binding, timeout_seconds=0)
    assert page['items'] == []
    assert page['next_cursor']
    assert (await observations.get_item(item.id)).exposed_wake_id is None
