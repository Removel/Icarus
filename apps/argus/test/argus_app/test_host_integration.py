import hashlib
import hmac
import json
import os
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from argus_app.application import create_app
from argus_app.config import ArgusConfig
from argus_app.infrastructure.store import Store


@pytest.mark.asyncio
async def test_real_host_grouped_plugins_two_tables_and_confirmation_gate(tmp_path, database_url):
    url = os.environ["ARGUS_TEST_DATABASE_URL"]
    store = Store(url)
    config = ArgusConfig(database_url=SecretStr(url), data_dir=tmp_path,
                         agent_token=SecretStr("agent-test"), gateway_token=SecretStr("gateway-test"),
                         qq_token=SecretStr("qq-test"), auto_scan=False)
    class AcceptedBridge:
        async def ensure_session(self, channel_id, request_id):
            return "session-test"
        async def wake(self, **payload):
            return {"task_id": "task-test", "status": "accepted"}
        async def status(self, wake_id):
            return {"task_id": "task-test", "status": "running"}
    app = create_app(config, store=store, bridge_client=AcceptedBridge())
    auth = {"Authorization": "Bearer agent-test"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://argus.test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/channels")).status_code == 401
            invalid = await client.post("/agent/events", headers=auth,
                json={"binding_id": "unknown", "request_id": "bad-kind", "kind": "not-a-control", "payload": {}})
            assert invalid.status_code == 422
            plugin_ids = app.state.host.plugin_manager.registry.plugin_ids()
            assert len(plugin_ids) == 11
            assert "protocols" not in plugin_ids and "rule-capabilities" not in plugin_ids
            group_id = uuid4().int % 100000000000
            source = f"qq:123:group:{group_id}"
            response = await client.post("/channels", headers=auth, json={"title": "review", "goal": "wait", "source_keys": [source],
                "rule_source": 'def main(event, channel):\n    return {"passed": True, "reason": "test"}\n'})
            assert response.status_code == 200, response.text
            channel_id = response.json()["id"]
            raw = json.dumps({"post_type": "message", "self_id": 123, "message_type": "group", "group_id": group_id,
                              "user_id": 789, "message_id": str(uuid4()), "raw_message": "review ready"}).encode()
            signature = "sha1=" + hmac.new(b"qq-test", raw, hashlib.sha1).hexdigest()
            response = await client.post("/protocols/qq", content=raw, headers={"X-Signature": signature})
            assert response.status_code == 200, response.text
            item_id = response.json()["observation_id"]
            await app.state.host.plugin_manager.event_bus.drain()
            await app.state.host.get_plugin("workflow-rules").process_channel(channel_id)
            round_ = await store.begin_round(channel_id)
            assert round_ and item_id in round_["observation_ids"]
            await store.set_round_delivery(channel_id, round_["wake_id"], "session-test", "task-test")
            async def control(kind, payload):
                response = await client.post("/agent/events", headers=auth, json={"binding_id": round_["binding_id"],
                    "request_id": str(uuid4()), "kind": kind, "payload": payload})
                assert response.status_code == 200, response.text
                return response.json()
            current = await store.channel(channel_id)
            rejected = await control('agent.commit_requested', {'expected_version': current.version,
                'results': [{'observation_id': item_id, 'status': 'handled'}], 'progress': 'done'})
            assert rejected['applied'] is False and rejected['code'] == 'events_not_delivered'
            assert (await control('agent.delivered', {'observation_ids': [item_id]}))['applied']
            committed = await control('agent.commit_requested', {'expected_version': current.version,
                'results': [{'observation_id': item_id, 'status': 'handled'}], 'progress': 'done'})
            assert committed['committed'] is True
            assert (await store.channel(channel_id)).progress == 'done'
            assert (await store.channel(channel_id)).runtime['round'] is None
