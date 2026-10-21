import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

pytestmark = pytest.mark.integration


def test_seed_and_smoke_use_public_rest_contract(client):
    path = Path(__file__).resolve().parents[2] / "scripts/seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.seed(client)
    second = module.seed(client)
    assert first == second
    assert client.get(f"/v1/persons/{first['person_a']}/profile", params={"q": "猫罐头"}).json()["items"]
    response = client.get("/v1/relations", params={"person_id": first["person_a"]})
    assert len(response.json()["items"]) == 1
