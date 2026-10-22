"""Opt-in real mode mounts authenticated data routes without changing default mock mode."""

from httpx import ASGITransport, AsyncClient

from apps.argus.src.core.config import ArgusConfig


async def test_real_mode_mounts_internal_service_and_keeps_normal_reads_local(database, monkeypatch):
    from apps.argus.src.main import create_app, create_service_app
    from apps.argus.src.common.schemas.judgement import JudgementOutcome
    from apps.argus.src.common.schemas.observation import SelectedItem
    from apps.argus.test.factories import draft_factory
    monkeypatch.setenv('ARGUS_AGENT_API_TOKEN', 'fixture-token')
    monkeypatch.setenv('ARGUS_GATEWAY_TOKEN', 'fixture-control-token')
    config = ArgusConfig(execution={'mode': 'gateway'})
    app = create_app(config, database=database)
    async with app.router.lifespan_context(app):
        service_app = create_service_app(app)
        async with AsyncClient(transport=ASGITransport(app=service_app), base_url='http://service') as client:
            assert (await client.get('/api/argus/observed-items/00000000-0000-0000-0000-000000000001')).status_code == 404
            assert (await client.post('/api/argus/internal/wakes/00000000-0000-0000-0000-000000000001/read', json={})).status_code == 401
        assert app.state.handoff_service is not None
