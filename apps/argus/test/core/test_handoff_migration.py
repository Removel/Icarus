"""Explicit v1 migration keeps observations, mock identities and table boundaries."""

from uuid import uuid4

from sqlalchemy import text

from apps.argus.src.common.repositories.observation_repository import ObservationRepository
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.src.core.database import Database
from apps.argus.test.common.repositories.test_observation_repository import draft


async def test_v2_to_v3_concurrent_migration_preserves_frozen_body_and_reports(database):
    import asyncio
    from apps.argus.test.common.repositories.test_handoff_repository import bound
    _, handoff, first, wake, binding = await bound(database)
    await handoff.mark_exposed(binding, [first.id])
    receipt = await handoff.report(binding, {'report_id': 'legacy', 'items': [
        {'observation_id': str(first.id), 'status': 'handled'}]})
    async with database.begin() as db:
        saved = (await db.execute(text('SELECT payload, handoff FROM wake_requests'))).one()
        await db.execute(text('UPDATE schema_version SET version=2'))
    instances = [Database(database.dsn, schema=database.schema) for _ in range(2)]
    try:
        await asyncio.gather(*(instance.start() for instance in instances))
        async with instances[0].session() as db:
            after = (await db.execute(text('SELECT payload, handoff FROM wake_requests'))).one()
            assert after == saved
            assert after.handoff['report_log']['legacy']['receipt'] == receipt
            assert await db.scalar(text('SELECT version FROM schema_version')) == 3
    finally:
        await asyncio.gather(*(instance.close() for instance in instances))


async def test_schema_v2_has_only_two_new_columns_and_preserves_v1_data(database):
    repo = ObservationRepository(database, boot_id=uuid4())
    item, _ = await repo.upsert_revision(draft())
    wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
        selected=(SelectedItem(item=item, excerpt='hello'),), decision='wake', reason='legacy'))
    async with database.begin() as session:
        await session.execute(text('ALTER TABLE wake_requests DROP COLUMN IF EXISTS handoff CASCADE'))
        await session.execute(text('ALTER TABLE observed_items DROP COLUMN IF EXISTS exposed_wake_id'))
        await session.execute(text('DROP INDEX IF EXISTS ix_observed_scope_watermark'))
        await session.execute(text('UPDATE schema_version SET version=1'))
    await database.close()
    migrated = Database(database.dsn, schema=database.schema)
    await migrated.start()
    try:
        async with migrated.session() as session:
            assert await session.scalar(text('SELECT version FROM schema_version')) == 3
            assert await session.scalar(text('SELECT count(*) FROM observed_items')) == 1
            assert await session.scalar(text('SELECT count(*) FROM wake_requests')) == 1
            saved = (await session.execute(text('SELECT status, payload, handoff FROM wake_requests'))).one()
            assert saved.status == 'waiting'
            assert saved.payload['wake_id'] == str(wake.id)
            assert saved.handoff['mode'] == 'mock'
            tables = set((await session.execute(text(
                'SELECT tablename FROM pg_tables WHERE schemaname=current_schema()'))).scalars())
            assert tables == {'schema_version', 'observed_items', 'wake_requests'}
    finally:
        await migrated.close()
