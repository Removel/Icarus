from datetime import UTC, datetime, timedelta

import pytest
from conftest import person

pytestmark = pytest.mark.integration


def test_profile_tombstone_does_not_resurrect_old_value(client):
    pid = person(client)
    url = f"/v1/persons/{pid}/profile/cat_food_preference"
    for value in ("喜欢鱼肉味猫罐头", "喜欢鸡肉味猫罐头"):
        response = client.put(url, json={"value": value, "source": "agent", "source_ref": "mem0:same-source"})
        assert response.status_code == 200, response.text
    current = client.get(f"/v1/persons/{pid}/profile", params={"q": "猫罐头"}).json()["items"]
    assert [item["attr_value"] for item in current] == ["喜欢鸡肉味猫罐头"]
    assert client.get(f"/v1/persons/{pid}/profile", params={"q": "鱼肉"}).json()["items"] == []
    assert client.delete(url, params={"source": "user", "source_ref": "correction"}).status_code == 204
    assert client.get(f"/v1/persons/{pid}/profile").json()["items"] == []
    assert client.get(f"/v1/persons/{pid}/profile", params={"q": "鱼肉"}).json()["items"] == []
    history = client.get(url + "/history").json()["items"]
    assert [item["status"] for item in history] == ["deleted", "active", "active"]
    assert history[0]["source"] == "user"
    assert client.put(url, json={"value": None, "source": "user"}).status_code == 200
    current = client.get(f"/v1/persons/{pid}/profile").json()["items"]
    assert current[0]["attr_value"] is None
    assert client.get(f"/v1/persons/{pid}/profile/keys").json()["items"] == ["cat_food_preference"]


def test_profile_filters_paging_and_history_backfill(client):
    pid = person(client)
    base = f"/v1/persons/{pid}/profile"
    for key in ("hobbies", "pet.food", "height_cm"):
        assert client.put(base + "/" + key, json={"value": "摄影100%_", "source": "user"}).status_code == 200
    first = client.get(base, params={"limit": 1}).json()
    second = client.get(base, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert first["items"][0]["attr_key"] != second["items"][0]["attr_key"]
    assert len(client.get(base, params={"q": "%_"}).json()["items"]) == 3
    assert client.get(base, params={"q": "not-found"}).json()["items"] == []
    assert len(client.get(base, params={"keys": "hobbies"}).json()["items"]) == 1
    response = client.put(base + "/hobbies", json={"value": "旧内容", "source": "import", "recorded_at": "2000-01-01T00:00:00Z"})
    assert response.status_code == 200
    assert client.get(base, params={"keys": "hobbies"}).json()["items"][0]["attr_value"] == "摄影100%_"


def test_profile_write_rejects_unknown_person_and_bad_inputs(client):
    assert client.put("/v1/persons/missing/profile/a", json={"value": "x", "source": "user"}).status_code == 404
    pid = person(client)
    url = f"/v1/persons/{pid}/profile/field"
    for time in ("2000-01-01T00:00:00", "2099-01-01T00:00:00Z"):
        assert client.put(url, json={"value": "x", "source": "user", "recorded_at": time}).status_code == 400
    assert client.put(f"/v1/persons/{pid}/profile/Bad-Key", json={"value": 1, "source": "user"}).status_code == 400
    assert client.delete(url).status_code == 400
