import json

import pytest
from pydantic import ValidationError


def test_invalid_configuration_diagnostics_do_not_render_secrets(tmp_path):
    from config import load_settings

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"identity": {"user": {"person_id": "me", "display_name": "me"}}}))
    with pytest.raises(ValidationError) as caught:
        load_settings(path, {"RELA_MEM_API_TOKEN": "", "RELA_MEM_FALKORDB_PASSWORD": "must-not-log-graph-password", "RELA_MEM_PG_PASSWORD": "pgleak"})
    assert "pgleak" not in str(caught.value)
    assert "must-not-log" not in str(caught.value)


@pytest.mark.integration
async def test_existing_profile_table_without_id_default_is_rejected(settings):
    from sqlalchemy import text
    from repositories.database import Database
    from errors import ServiceError
    db = Database(settings)
    schema = settings.postgres.schema_name
    try:
        async with db.engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'''CREATE TABLE "{schema}".profile_entries (
                id BIGINT NOT NULL, person_id TEXT NOT NULL, attr_key TEXT NOT NULL,
                attr_value JSONB NOT NULL, source TEXT NOT NULL, source_ref TEXT,
                status TEXT NOT NULL DEFAULT 'active', recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )'''))
        with pytest.raises(ServiceError, match="incompatible"):
            await db.initialize()
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await db.close()


@pytest.mark.integration
def test_stale_relation_patch_cannot_resurrect_invalidated_relation(client, monkeypatch):
    from conftest import person
    a, b = person(client), person(client, "李四")
    value = {"from_person_id": a, "to_person_id": b, "kind": "work", "direction": "one_way", "forward_detail": "上司", "source": "user"}
    rid = client.post("/v1/relations", json=value).json()["relation"]["relation_id"]
    repository = client.app.state.relations.repository
    original = repository.replace

    async def interleave(*args, **kwargs):
        await client.app.state.graph.query("MATCH ()-[r:RELATED_TO {relation_id:$id}]->() SET r.status='invalid' RETURN r", {"id": rid})
        return await original(*args, **kwargs)

    monkeypatch.setattr(repository, "replace", interleave)
    response = client.patch(f"/v1/relations/{rid}", json={"source_ref": "new-source"})
    assert response.status_code == 409
    assert client.get("/v1/relations").json()["items"] == []
    assert len(client.get("/v1/relations", params={"status": "invalid"}).json()["items"]) == 1


@pytest.mark.integration
def test_relation_post_explicit_null_clears_old_provenance(client):
    from conftest import person
    a, b = person(client), person(client, "李四")
    data = {"from_person_id": a, "to_person_id": b, "kind": "work", "direction": "one_way", "forward_detail": "上司", "source": "import", "source_ref": "old-batch"}
    assert client.post("/v1/relations", json=data).status_code == 200
    data.update(source="user", source_ref=None)
    response = client.post("/v1/relations", json=data)
    assert response.status_code == 200
    assert response.json()["relation"].get("source_ref") is None


@pytest.mark.integration
@pytest.mark.parametrize("position", ["{}", "[]", '["2000-01-01T00:00:00",1]', '["2000-01-01T00:00:00Z",9223372036854775808]', '["2000-01-01T00:00:00Z",true]'])
def test_malformed_history_position_is_validation_error(client, position):
    from utils.pagination import encode_cursor
    filters = {"person_id": "removel", "key": "hobbies", "include_archived": False}
    response = client.get("/v1/persons/removel/profile/hobbies/history", params={"cursor": encode_cursor(position, filters)})
    assert response.status_code == 400, response.text
