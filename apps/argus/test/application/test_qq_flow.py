import asyncio
import hashlib
import hmac
import json

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.argus.src.clients.JevClient import Judgement
from apps.argus.src.config.config_model import ArgusConfig, QQConfig
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.main import create_app


class FakeJudge:
    async def decide(self, *, state, questions, question, outcomes, fallback):
        return Judgement("wake" if "叫醒" in state["content"] else "silence")


class FakeGateway:
    def __init__(self):
        self.submissions = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def session_create(self, path, session_id):
        return {"session_id": session_id, "workspace_key": "server-key", "lifecycle": "ready"}

    async def session_get(self, path, session_id):
        return {"session_id": session_id, "workspace_key": "server-key", "lifecycle": "ready"}

    async def session_submit(self, path, session_id, prompt, submission_id):
        self.submissions.append((session_id, prompt, submission_id))
        return {"task_id": "task-1", "queue_position": 0}


def signed(body):
    return {
        "X-Self-ID": "10000",
        "X-Signature": "sha1=" + hmac.new(b"test-secret", body, hashlib.sha1).hexdigest(),
    }


def event(message_id, message):
    return json.dumps({
        "post_type": "message", "self_id": 10000, "time": int(datetime.now(UTC).timestamp()),
        "message_id": message_id, "message_type": "group", "group_id": 42,
        "user_id": 7, "message": [{"type": "text", "data": {"text": message}}],
    }, ensure_ascii=False).encode()


def test_signed_qq_event_flows_through_worker_to_gateway(tmp_path):
    config = ArgusConfig(
        host="127.0.0.1", port=8766, log_level="INFO", queue_capacity=5, worker_count=1,
        qq=QQConfig(enabled=True, bot_id="10000", workspace_path=str(tmp_path),
                    gateway_url="ws://127.0.0.1:8765/rpc"),
    )
    store = ObservationStore(tmp_path)
    gateway = FakeGateway()
    app = create_app(config, store=store, judge=FakeJudge(), gateway=gateway,
                     qq_secret="test-secret")
    with TestClient(app) as client:
        first = event(401, "叫醒我")
        assert client.post("/onebot/events", content=first, headers=signed(first)).status_code == 204
        client.portal.call(app.state.queue.join)
        rows = client.portal.call(store.recent, "qq", "10000", "qq:group:42")
        assert len(rows) == 1
        assert client.get(f"/observations/{rows[0].id}/status").json()["status"] == "submitted"
        assert len(gateway.submissions) == 1
        again = client.post("/onebot/events", content=first, headers=signed(first))
        assert again.status_code == 204
        assert len(gateway.submissions) == 1
        second = event(402, "普通聊天")
        assert client.post("/onebot/events", content=second, headers=signed(second)).status_code == 204
        client.portal.call(app.state.queue.join)
        assert len(gateway.submissions) == 1
