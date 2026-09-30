import asyncio

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from apps.argus.src.main import create_app


def test_health_tracks_worker_lifecycle():
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ready"}
        app.state.worker.task.cancel()
        assert client.get("/health").status_code == 503

    assert not app.state.worker.is_running


def test_disabled_qq_does_not_consume_existing_pending_records(tmp_path):
    from apps.argus.src.dao.observation_store import ObservationStore
    from apps.argus.src.handler.qq_handler import QQHandler
    from apps.argus.src.config.config_model import ArgusConfig

    store = ObservationStore(tmp_path)
    async def seed():
        await store.start()
        obs = QQHandler("10000").parse({
            "post_type": "message", "self_id": 10000, "time": 1700000000,
            "message_id": 71, "message_type": "private", "user_id": 7,
            "message": "保留",
        })
        await store.insert(obs)
        await store.close()
        return obs.id
    observation_id = asyncio.run(seed())
    paused_store = ObservationStore(tmp_path)
    recover_calls = []
    original_recover = paused_store.unassigned_scopes
    async def count_recover(*args, **kwargs):
        recover_calls.append(True)
        return await original_recover(*args, **kwargs)
    paused_store.unassigned_scopes = count_recover
    config = ArgusConfig(host="127.0.0.1", port=8766, log_level="INFO", queue_capacity=1, worker_count=1)
    with TestClient(create_app(config, store=paused_store)) as client:
        assert client.get("/health").status_code == 200
        assert client.get(f"/observations/{observation_id}/status").json()["status"] == "pending"
        assert not recover_calls


def test_health_before_start_is_not_ready():
    app = create_app()

    async def request_without_lifespan():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/health")

    assert asyncio.run(request_without_lifespan()).status_code == 503
