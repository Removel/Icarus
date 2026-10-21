from uuid import uuid4

from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.surfaces.qq.services.observation_service import QQObservationService
from apps.argus.src.surfaces.qq.services.parser_service import QQParser


async def test_duplicate_event_notifies_only_once(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    calls = []
    service = QQObservationService(repo, QQParser('123'), notify=lambda chat, **kwargs: calls.append((chat, kwargs)))
    event = {'self_id': 123, 'post_type': 'message', 'message_type': 'private', 'message_id': 1,
             'user_id': 456, 'message': 'hi', 'time': 1700000000}
    first, inserted = await service.receive(event)
    repeated, again = await service.receive(event)
    assert inserted and not again and first.id == repeated.id
    assert calls == [('qq:123:private:456', {'immediate': True})]
