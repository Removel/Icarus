import pytest

pytestmark = pytest.mark.integration


def test_health_bootstrap_and_authentication(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    response = client.get("/v1/persons")
    assert {item["kind"] for item in response.json()["items"]} == {"user", "agent"}
    assert client.get("/v1/persons/removel").json()["person"]["display_name"] == "鱼梦"
    client.headers.pop("Authorization")
    assert client.get("/v1/persons").status_code == 401
    client.headers["Authorization"] = "Bearer wrong"
    assert client.get("/v1/persons").status_code == 401


def test_unknown_fields_and_sensitive_validation_are_safe(client):
    response = client.post("/v1/persons", json={"display_name": "x", "password": "secret-do-not-echo"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "secret-do-not-echo" not in response.text
