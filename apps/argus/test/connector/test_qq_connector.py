import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.argus.src.connector.qq_connector import QQConnector
from apps.argus.src.config.config_model import QQConfig
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService


SECRET = "local-test-key"


def receiving_handler(store):
    service = QQService(store,None,None,workspace_path=str(store.database.data_dir))
    return QQHandler('10000',receive=service.receive)


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

    config = QQConfig(enabled=True, bot_id="10000", workspace_path=str(tmp_path),
                      gateway_url="ws://127.0.0.1:8765/rpc")
    app = FastAPI()
    app.include_router(QQConnector(config, SECRET, receiving_handler(store)).router)
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

    config = QQConfig(enabled=True, bot_id="10000", workspace_path=str(tmp_path),
                      gateway_url="ws://127.0.0.1:8765/rpc")
    app = FastAPI()
    app.include_router(QQConnector(config, SECRET, receiving_handler(store)).router)
    body = payload()
    with TestClient(app) as client:
        response = client.post("/onebot/events", content=body, headers=mutate(body, signed_headers(body)))
        assert response.status_code != 204
    assert asyncio.run(store.recent("qq", "10000", "qq:group:42")) == ()
    asyncio.run(store.close())


@pytest.mark.parametrize('event', [
    {'post_type': 'meta_event', 'meta_event_type': 'heartbeat', 'status': {'online': True}, 'interval': 5000},
    {'post_type': 'meta_event', 'meta_event_type': 'lifecycle', 'sub_type': 'connect'},
    {'post_type': 'notice', 'notice_type': 'notify', 'sub_type': 'poke', 'group_id': 42, 'user_id': 7, 'target_id': 10000},
    {'post_type': 'request', 'request_type': 'friend', 'user_id': 7, 'flag': 'test-flag'},
])
def test_recognized_nonchat_event_is_acknowledged_without_storage(tmp_path, event):
    store = ObservationStore(tmp_path)
    asyncio.run(store.start())
    try:

        config = QQConfig(enabled=True, bot_id='10000', workspace_path=str(tmp_path),
                          gateway_url='ws://127.0.0.1:8765/rpc')
        app = FastAPI()
        app.include_router(QQConnector(config, SECRET, receiving_handler(store)).router)
        body = json.dumps(dict(event, self_id=10000, time=1700000000)).encode()
        with TestClient(app) as client:
            response = client.post('/onebot/events', content=body, headers=signed_headers(body))
            assert response.status_code == 204
            assert response.content == b''
        assert asyncio.run(store.unassigned_scopes()) == ()
    finally:
        asyncio.run(store.close())


@pytest.mark.parametrize('event', [
    {'post_type': 'message', 'self_id': 10000, 'message_type': 'group'},
    {'post_type': 'meta_event', 'self_id': 20000, 'meta_event_type': 'heartbeat'},
    {'post_type': 'unknown', 'self_id': 10000},
    ['not', 'an', 'object'],
])
def test_invalid_event_is_not_acknowledged_as_ignored(tmp_path, event):
    store = ObservationStore(tmp_path)
    asyncio.run(store.start())
    try:
        config = QQConfig(enabled=True, bot_id='10000', workspace_path=str(tmp_path),
                          gateway_url='ws://127.0.0.1:8765/rpc')
        app = FastAPI()
        app.include_router(QQConnector(config, SECRET, receiving_handler(store)).router)
        body = json.dumps(event).encode()
        with TestClient(app) as client:
            assert client.post('/onebot/events', content=body, headers=signed_headers(body)).status_code == 422
        assert asyncio.run(store.unassigned_scopes()) == ()
    finally:
        asyncio.run(store.close())


def test_ignored_event_still_requires_valid_signature(tmp_path):
    store = ObservationStore(tmp_path)
    asyncio.run(store.start())
    try:
        config = QQConfig(enabled=True, bot_id='10000', workspace_path=str(tmp_path),
                          gateway_url='ws://127.0.0.1:8765/rpc')
        app = FastAPI()
        app.include_router(QQConnector(config, SECRET, receiving_handler(store)).router)
        body = json.dumps({'post_type': 'meta_event', 'self_id': 10000, 'meta_event_type': 'heartbeat'}).encode()
        headers = dict(signed_headers(body), **{'X-Signature': 'sha1=' + '0' * 40})
        with TestClient(app) as client:
            assert client.post('/onebot/events', content=body, headers=headers).status_code == 401
    finally:
        asyncio.run(store.close())
