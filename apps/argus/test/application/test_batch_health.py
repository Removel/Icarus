import asyncio
from fastapi.testclient import TestClient
from apps.argus.src.config.config_model import ArgusConfig
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.src.main import create_app


def test_health_reports_closed_database_as_unavailable(tmp_path):
    store = ObservationStore(tmp_path)
    config = ArgusConfig(host='127.0.0.1', port=8766, log_level='INFO', queue_capacity=5, worker_count=1)
    with TestClient(create_app(config, database=store.database)) as client:
        assert client.get('/health').status_code == 200
        client.portal.call(store.close)
        assert client.get('/health').status_code == 503


def test_health_checks_database_not_only_session_factory(tmp_path):
    store = ObservationStore(tmp_path)
    config = ArgusConfig(host='127.0.0.1', port=8766, log_level='INFO', queue_capacity=5, worker_count=1)
    async def unavailable():
        return False
    with TestClient(create_app(config, database=store.database)) as client:
        store.database.is_available = unavailable
        assert client.get('/health').status_code == 503
