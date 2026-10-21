from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from apps.argus.src.core.config import ArgusConfig, QQConfig
from apps.argus.src.common.schemas.judgement import JudgementOutcome
from apps.argus.src.common.schemas.observation import SelectedItem
from apps.argus.test.factories import draft_factory


async def test_history_readable_but_never_currently_active(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(), database=database)
    async with app.router.lifespan_context(app):
        repo = app.state.repository
        item, _ = await repo.upsert_revision(draft_factory())
        wake = await repo.commit_judgement(item.chat_id, JudgementOutcome(
            selected=(SelectedItem(item=item, excerpt='hello'),), decision='wake', reason='test'))
        now = datetime.now(UTC)
        await repo.mark_accepted(wake.id, now=now, deadline_at=now + timedelta(seconds=60))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get(f'/api/argus/wake-requests/{wake.id}')
            assert response.status_code == 200
            assert response.json()['wake']['status'] == 'accepted'
            assert response.json()['wake']['active'] is False
            assert (await client.get(f'/api/argus/observed-items/{item.id}')).status_code == 200
            assert (await client.get(f'/api/argus/chats/{item.chat_id}/unhandled')).json() == []
            assert (await client.get('/api/argus/wake-requests?limit=101')).status_code == 422
            assert (await client.get('/api/argus/observed-items/00000000-0000-0000-0000-000000000001')).status_code == 404


async def test_dead_scheduler_and_closed_database_are_unhealthy(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(qq=QQConfig(enabled=True, bot_id='123')), database=database, qq_secret='secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            app.state.scheduler_task.cancel()
            import asyncio
            await asyncio.gather(app.state.scheduler_task, return_exceptions=True)
            assert (await client.get('/health')).status_code == 503
            await database.close()
            assert (await client.get('/api/argus/health')).status_code == 503
