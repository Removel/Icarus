import asyncio

import pytest

pytestmark = pytest.mark.integration


def test_changed_special_identity_configuration_does_not_create_second_user(client, settings):
    from config import Identity
    from errors import ServiceError
    from repositories.person_repo import PersonRepository

    identity = Identity.model_validate({"user": {"person_id": "another", "display_name": "another"},
                                       "agent": {"person_id": "icarus", "display_name": "Icarus"}})
    async def check():
        repository = PersonRepository(client.app.state.graph)
        with pytest.raises(ServiceError):
            await repository.bootstrap(identity)
        rows = await client.app.state.graph.query("MATCH (p:Person {kind:'user'}) RETURN p")
        assert len(rows) == 1
        assert rows[0][0].properties["person_id"] == "removel"
    client.portal.call(check)


def test_initialization_is_repeatable_and_name_aliases_are_not_unique(client):
    from repositories.initialization import initialize_graph
    from conftest import person
    client.portal.call(initialize_graph, client.app.state.graph)
    person(client, aliases=[{"namespace": "name", "value": "同名"}])
    person(client, aliases=[{"namespace": "name", "value": "同名"}])
    assert len(client.get("/v1/persons", params={"query": "同名"}).json()["items"]) == 2


def test_concurrent_identity_creation_is_unique(client):
    from concurrent.futures import ThreadPoolExecutor

    def create(index):
        return client.post("/v1/persons", json={"display_name": f"candidate-{index}", "aliases": [{"namespace": "qq", "value": "98765"}]}).status_code
    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(create, range(4)))
    assert sorted(statuses) == [201, 409, 409, 409]
    assert len(client.get("/v1/persons", params={"query": "candidate"}).json()["items"]) == 1
