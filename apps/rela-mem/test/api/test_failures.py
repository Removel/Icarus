from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import OperationalError


def test_startup_waits_for_operational_constraints():
    import asyncio
    from repositories.initialization import CONSTRAINTS, initialize_graph

    class Graph:
        name = "isolated"
        calls = 0

        async def query(self, query, params=None):
            if "YIELD label, properties, entitytype RETURN" in query:
                return [[label, [prop], entity] for entity, label, prop in CONSTRAINTS]
            if "db.indexes" in query:
                return [["OPERATIONAL"]]
            if "YIELD label, properties, entitytype, status" in query:
                return [[label, [prop], entity, "PENDING"] for entity, label, prop in CONSTRAINTS]
            self.calls += 1
            return [["PENDING" if self.calls == 1 else "OPERATIONAL"] for _ in CONSTRAINTS]

    graph = Graph()
    asyncio.run(initialize_graph(graph))
    assert graph.calls == 2


def test_failed_constraints_do_not_start_application(settings):
    from errors import ServiceError
    from main import create_app

    class Graph:
        closed = False
        async def query(self, query, params=None):
            from repositories.initialization import CONSTRAINTS
            if "db.indexes" in query:
                if "YIELD status" in query:
                    return [["OPERATIONAL"]]
                return [[label, [prop], entity] for entity, label, prop in CONSTRAINTS]
            if "YIELD label, properties, entitytype, status" in query:
                return [[label, [prop], entity, "FAILED"] for entity, label, prop in CONSTRAINTS]
            return [["FAILED"]]
        async def close(self):
            self.closed = True

    class Database:
        closed = False
        async def close(self):
            self.closed = True

    graph, database = Graph(), Database()
    with pytest.raises(ServiceError, match="constraint validation failed"):
        with TestClient(create_app(settings, graph=graph, database=database)):
            pass
    assert graph.closed and database.closed


def test_health_and_profile_outage_do_not_leak_secrets(settings):
    from main import create_app
    from errors import ServiceError

    app = create_app(settings)
    class Graph:
        async def ping(self):
            raise ServiceError("UNAVAILABLE", "graph store unavailable", 503)
    app.state.graph = Graph()
    class Database:
        async def ping(self):
            return None
    app.state.database = Database()
    app.state.settings = settings
    class Profiles:
        async def current(self, *args):
            raise OperationalError("SELECT", {}, Exception("sensitive-pg-password"))
    app.state.profiles = Profiles()
    client = TestClient(app)
    assert client.get("/healthz").status_code == 503
    response = client.get("/v1/persons/p_a/profile", headers={"Authorization": "Bearer integration-test-token"})
    assert response.status_code == 503
    assert "sensitive-pg-password" not in response.text


def test_openapi_is_available_to_authenticated_callers(settings):
    from main import create_app
    app = create_app(settings)
    app.state.settings = settings
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 401
    response = client.get("/openapi.json", headers={"Authorization": "Bearer integration-test-token"})
    assert response.status_code == 200
    assert "/v1/persons/{person_id}/profile" in response.json()["paths"]
