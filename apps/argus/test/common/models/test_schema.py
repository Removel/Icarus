from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_observation_schema_preserves_identity_and_rejects_extra_fields():
    from apps.argus.src.common.schemas.observation import ContentPart, ObservedItemDraft
    now = datetime.now(UTC)
    values = dict(source='qq', chat_id='chat', message_id='1', sender_id='2',
                  sent_at=now, observed_at=now, content=(ContentPart(kind='text', value='hi'),), locator={})
    value = ObservedItemDraft(**values)
    with pytest.raises(ValidationError):
        value.source = 'other'
    with pytest.raises(ValidationError):
        ObservedItemDraft(**values, boot_id=uuid4())


async def test_orm_columns_match_authoritative_database_schema(database):
    from sqlalchemy import inspect
    from apps.argus.src.common.models.observation import ObservationRow
    from apps.argus.src.common.models.wake import WakeRow
    async with database.session() as session:
        connection = await session.connection()
        for model in (ObservationRow, WakeRow):
            actual = await connection.run_sync(lambda sync: inspect(sync).get_columns(model.__tablename__))
            assert {column['name'] for column in actual} == set(model.__table__.columns.keys())
        indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes('wake_requests'))
    active = next(value for value in indexes if value['name'] == 'uq_wake_active')
    assert active['unique'] is True
    assert active['column_names'] == ['boot_id', 'chat_id']


def test_outcome_rejects_duplicate_item_ids():
    from apps.argus.src.common.schemas.wake import WakeOutcome
    identifier = uuid4()
    with pytest.raises(ValidationError):
        WakeOutcome(wake_id=uuid4(), item_ids=(identifier, identifier),
                    status='resolved', simulated=True, reason='mock')
