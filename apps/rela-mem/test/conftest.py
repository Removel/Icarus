import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def settings():
    from config import Settings

    return Settings.model_validate({
        "identity": {"user": {"person_id": "removel", "display_name": "鱼梦"},
                     "agent": {"person_id": "icarus", "display_name": "Icarus"}},
        "falkordb": {"port": int(os.environ.get("RELA_TEST_GRAPH_PORT", "26379")),
                     "graph": "rela_mem_test_" + uuid4().hex[:12]},
        "postgres": {"port": int(os.environ.get("RELA_TEST_PG_PORT", "25432")),
                     "database": "rela_mem_test", "schema": "test_" + uuid4().hex[:12]},
        "api_token": "integration-test-token",
        "graph_password": "rela-test-graph-password",
        "pg_password": "rela-test-pg-password",
    })


@pytest.fixture
def client(settings):
    from main import create_app

    with TestClient(create_app(settings)) as value:
        value.headers["Authorization"] = "Bearer integration-test-token"
        yield value
        # Cleanup stays in tests; names are generated here, never production names.
        import anyio
        from sqlalchemy import text

        async def cleanup():
            await value.app.state.graph.graph.delete()
            async with value.app.state.database.engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{settings.postgres.schema_name}" CASCADE'))
        value.portal.call(cleanup)


def person(client, name="张三", aliases=None, kind="human"):
    response = client.post("/v1/persons", json={"display_name": name, "kind": kind, "aliases": aliases or []})
    assert response.status_code == 201, response.text
    return response.json()["person"]["person_id"]
