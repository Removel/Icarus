import pytest
from conftest import person

pytestmark = pytest.mark.integration


def test_virtual_member_and_person_circle_listing(client):
    owner = "removel"
    virtual = person(client, "无主档案", kind="virtual")
    response = client.post("/v1/circles", json={"name": "家庭", "kind": "family", "owner_person_id": owner})
    assert response.status_code == 201, response.text
    circle = response.json()["circle"]["circle_id"]
    url = f"/v1/circles/{circle}/members/{virtual}"
    for alias in ("猫猫", "团子"):
        response = client.put(url, json={"alias": alias, "role": "pet", "identity_label": "宠物"})
        assert response.status_code == 200, response.text
    members = client.get(f"/v1/circles/{circle}/members").json()["items"]
    assert len(members) == 1
    assert members[0]["alias"] == "团子"
    assert members[0]["person"]["kind"] == "virtual"
    circles = client.get(f"/v1/persons/{virtual}/circles").json()["items"]
    assert circles[0]["circle"]["name"] == "家庭"
    assert client.delete(url).status_code == 204
    assert client.get(f"/v1/circles/{circle}/members").json()["items"] == []
    assert client.get(f"/v1/circles/{circle}/members", params={"include_left": True}).json()["items"][0]["status"] == "left"


def test_circle_archive_and_reference_validation(client):
    assert client.post("/v1/circles", json={"name": "x", "kind": "family", "owner_person_id": "missing"}).status_code == 404
    response = client.post("/v1/circles", json={"name": "班级", "kind": "class"})
    assert response.status_code == 201
    cid = response.json()["circle"]["circle_id"]
    assert client.put(f"/v1/circles/{cid}/members/missing", json={"role": "member"}).status_code == 404
    assert client.patch(f"/v1/circles/{cid}", json={"status": "archived"}).status_code == 200
    assert client.get("/v1/circles").json()["items"] == []
    assert client.get("/v1/circles", params={"include_archived": True}).json()["items"][0]["circle_id"] == cid
