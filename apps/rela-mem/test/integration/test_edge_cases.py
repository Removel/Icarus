import pytest
from conftest import person

pytestmark = pytest.mark.integration


def test_two_way_patch_requires_both_labels_in_request(client):
    a, b = person(client), person(client, "李四")
    body = {"from_person_id": a, "to_person_id": b, "kind": "family", "direction": "two_way",
            "forward_detail": "表哥", "reverse_detail": "表弟", "source": "user"}
    relation = client.post("/v1/relations", json=body).json()["relation"]
    url = f"/v1/relations/{relation['relation_id']}"
    response = client.patch(url, json={"forward_detail": "堂哥"})
    assert response.status_code == 400
    response = client.patch(url, json={"forward_detail": "堂哥", "reverse_detail": "堂弟"})
    assert response.status_code == 200


def test_history_cursor_uses_time_then_id_and_isolated_person_filter(client):
    a, b = person(client), person(client, "李四")
    url = f"/v1/persons/{a}/profile/hobbies"
    for value in ("摄影", "跑步", "爬山"):
        client.put(url, json={"value": value, "source": "agent", "recorded_at": "2000-01-01T00:00:00Z"})
    client.put(f"/v1/persons/{b}/profile/hobbies", json={"value": "不应出现", "source": "user"})
    first = client.get(url + "/history", params={"limit": 1}).json()
    assert first["items"][0]["attr_value"] == "爬山"
    second = client.get(url + "/history", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert second["items"][0]["attr_value"] == "跑步"
    assert client.get(url + "/history", params={"cursor": "bad"}).status_code == 400


def test_switch_direction_keeps_relation_id_and_correct_orientation(client):
    a, b = person(client), person(client, "李四")
    response = client.post("/v1/relations", json={"from_person_id": b, "to_person_id": a,
        "kind": "work", "direction": "one_way", "forward_detail": "上司", "source": "user"})
    rid = response.json()["relation"]["relation_id"]
    response = client.patch(f"/v1/relations/{rid}", json={"direction": "two_way", "forward_detail": "上司", "reverse_detail": "下属"})
    assert response.status_code == 200, response.text
    rows = client.get("/v1/relations", params={"person_id": a}).json()["items"]
    assert rows[0]["relation_id"] == rid
    assert rows[0]["detail"] == "上司"
