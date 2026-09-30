import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.argus.src.connector.qq_connector import QQConnector
from apps.argus.src.config.config_model import QQConfig
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.queue import InboundQueue


SECRET = "local-test-key"


def payload(message_id=301):
    return json.dumps({
        "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": message_id,
        "message_type": "group", "group_id": 42, "user_id": 7,
        "message": [{"type": "text", "data": {"text": "你好"}}],
    }, ensure_ascii=False).encode()


def signed_headers(body):
    signature = hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
    return {"X-Signature": f"sha1={signature}", "X-Self-ID": "10000"}


def test_valid_signed_event_is_persisted_once_with_empty_204(tmp_path):
    store = ObservationStore(tmp_path)
    asyncio.run(store.start())
    queue = InboundQueue(2)
    config = QQConfig(enabled=True, bot_id="10000", workspace_path=str(tmp_path),
                      gateway_url="ws://127.0.0.1:8765/rpc")
    app = FastAPI()
    app.include_router(QQConnector(config, SECRET, store, queue).router)
    body = payload()
    with TestClient(app) as client:
        response = client.post("/onebot/events", content=body, headers=signed_headers(body))
        assert response.status_code == 204
        assert response.content == b""
        assert "application/json" not in response.headers.get("content-type", "")
        again = client.post("/onebot/events", content=body, headers=signed_headers(body))
        assert again.status_code == 204
    rows = asyncio.run(store.recent("qq", "10000", "qq:group:42"))
    assert len(rows) == 1
    assert asyncio.run(queue.get()).id == rows[0].id
    queue.task_done()
    assert queue._items.empty()
    asyncio.run(store.close())


@pytest.mark.parametrize("mutate", [
    lambda body, headers: {**headers, "X-Signature": "sha1=" + "0" * 40},
    lambda body, headers: {**headers, "X-Signature": "bad"},
    lambda body, headers: {**headers, "X-Self-ID": "20000"},
    lambda body, headers: {"X-Self-ID": "10000"},
])
def test_invalid_signature_or_account_does_not_persist(tmp_path, mutate):
    store = ObservationStore(tmp_path)
    asyncio.run(store.start())
    queue = InboundQueue(1)
    config = QQConfig(enabled=True, bot_id="10000", workspace_path=str(tmp_path),
                      gateway_url="ws://127.0.0.1:8765/rpc")
    app = FastAPI()
    app.include_router(QQConnector(config, SECRET, store, queue).router)
    body = payload()
    with TestClient(app) as client:
        response = client.post("/onebot/events", content=body, headers=mutate(body, signed_headers(body)))
        assert response.status_code != 204
    assert asyncio.run(store.recent("qq", "10000", "qq:group:42")) == ()
    asyncio.run(store.close())
