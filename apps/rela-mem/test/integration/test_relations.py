import pytest
from conftest import person

pytestmark = pytest.mark.integration


def relation(client, source, target, **changes):
    data = {"from_person_id": source, "to_person_id": target, "kind": "kin", "direction": "two_way",
            "forward_detail": "表哥", "reverse_detail": "表弟", "source": "user"} | changes
    if data["direction"] == "one_way":
        data.pop("reverse_detail", None)
    return client.post("/v1/relations", json=data)


def test_reversed_two_way_upsert_preserves_details(client):
    a, b = person(client), person(client, "李四")
    first = relation(client, a, b)
    assert first.status_code == 200, first.text
    first = first.json()
    second = relation(client, b, a, forward_detail="表弟", reverse_detail="表哥").json()
    assert first["created"] is True
    assert second["created"] is False
    assert first["relation"]["relation_id"] == second["relation"]["relation_id"]
    for pid, detail in ((a, "表弟"), (b, "表哥")):
        response = client.get("/v1/relations", params={"person_id": pid})
        assert response.status_code == 200, response.text
        rows = response.json()["items"]
        assert len(rows) == 1
        assert rows[0]["detail"] == detail
        assert rows[0]["direction"] == "two_way"
        assert rows[0]["forward_detail"] and rows[0]["reverse_detail"]


def test_relation_scopes_direction_patch_conflict_and_invalidation(client):
    a, b = person(client), person(client, "李四")
    circle = client.post("/v1/circles", json={"name": "家庭", "kind": "family"}).json()["circle"]["circle_id"]
    global_relation = relation(client, a, b).json()["relation"]
    scoped_relation = relation(client, a, b, scope_circle_id=circle).json()["relation"]
    single = relation(client, a, b, direction="one_way", forward_detail="上司", kind="work").json()["relation"]
    assert len(client.get("/v1/relations", params={"person_id": a}).json()["items"]) == 3
    assert len(client.get("/v1/relations", params={"person_id": a, "circle_id": circle}).json()["items"]) == 1
    incoming = client.get("/v1/relations", params={"person_id": b, "kind": "work"}).json()["items"][0]
    outgoing = client.get("/v1/relations", params={"person_id": a, "kind": "work"}).json()["items"][0]
    assert incoming["detail"] == "上司"
    assert outgoing["detail"] is None and outgoing["detail_missing"] is True
    conflict = client.patch(f"/v1/relations/{scoped_relation['relation_id']}", json={"scope_circle_id": None})
    assert conflict.status_code == 409
    assert len(client.get("/v1/relations").json()["items"]) == 3
    assert client.delete(f"/v1/relations/{single['relation_id']}").status_code == 204
    assert len(client.get("/v1/relations").json()["items"]) == 2
    assert client.post("/v1/relations", json={"from_person_id": a, "to_person_id": b, "kind": "x", "direction": "two_way", "forward_detail": "朋友", "source": "user"}).status_code == 400


def test_paths_follow_active_edges_and_exclude_archived_people(client):
    a, b, c = person(client), person(client, "李四"), person(client, "王五")
    relation(client, a, b)
    relation(client, c, b, direction="one_way", kind="work")
    response = client.get("/v1/graph/shortest-path", params={"from": a, "to": c})
    assert response.status_code == 200, response.text
    data = response.json()
    assert [p["person_id"] for p in data["path"]] == [a, b, c]
    assert data["length"] == 2 and len(data["edges"]) == 2
    assert client.get("/v1/graph/shortest-path", params={"from": a, "to": c, "max_depth": 1}).json()["path"] == []
    client.patch(f"/v1/persons/{b}", json={"status": "archived"})
    assert client.get("/v1/graph/shortest-path", params={"from": a, "to": c}).json()["path"] == []
    assert client.get("/v1/relations", params={"person_id": a}).json()["items"] == []
    assert client.get("/v1/graph/shortest-path", params={"from": a, "to": c, "max_depth": 6}).status_code == 400
