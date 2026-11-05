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
    config = ArgusConfig(channel_naming_enabled=False, database_url=SecretStr(url), data_dir=tmp_path,
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


@pytest.mark.asyncio
async def test_configured_fallback_is_injected_and_closed_on_shutdown(tmp_path, database_url, monkeypatch):
    from argus_app.contracts.models import DiscoveryDecision
    class Owner:
        closed = 0
        async def aclose(self):
            self.closed += 1
    owner = Owner()
    async def fallback(text, candidates):
        return DiscoveryDecision(action='defer', reason='unused')
    builds = []
    def build(model_name, settings_path=None):
        builds.append((model_name, settings_path))
        return fallback, owner
    monkeypatch.setattr('argus_app.model_provider.discovery_fallback.configured_discovery_fallback', build)
    config = ArgusConfig(channel_naming_enabled=False, database_url=SecretStr(database_url), data_dir=tmp_path,
        agent_token=SecretStr('agent-test'), gateway_token=SecretStr('gateway-test'), auto_scan=False,
        discovery_fallback_enabled=True, discovery_fallback_model='perception')
    app = create_app(config)
    async with app.router.lifespan_context(app):
        assert app.state.host.get_plugin('workflow-rules').discovery_fallback is fallback
        assert owner.closed == 0
    assert owner.closed == 1
    assert builds == [('perception', None)]


@pytest.mark.asyncio
async def test_injected_fallback_is_not_owned_and_disabled_fallback_is_not_built(tmp_path, database_url, monkeypatch):
    class Fallback:
        closed = 0
        async def __call__(self, text, candidates):
            raise AssertionError('not invoked during startup')
        async def aclose(self):
            self.closed += 1
    fallback = Fallback()
    def unexpected_build(*args, **kwargs):
        raise AssertionError('must not build an injected or disabled backend')
    monkeypatch.setattr('argus_app.model_provider.discovery_fallback.configured_discovery_fallback', unexpected_build)
    for enabled in (True, False):
        config = ArgusConfig(channel_naming_enabled=False, database_url=SecretStr(database_url), data_dir=tmp_path,
            agent_token=SecretStr('agent-test'), gateway_token=SecretStr('gateway-test'), auto_scan=False,
            discovery_fallback_enabled=enabled, discovery_fallback_model='perception')
        app = create_app(config, discovery_fallback=fallback)
        async with app.router.lifespan_context(app):
            actual = app.state.host.get_plugin('workflow-rules').discovery_fallback
            assert actual is (fallback if enabled else None)
    assert fallback.closed == 0


@pytest.mark.asyncio
async def test_fallback_owner_is_closed_if_host_startup_fails(tmp_path, database_url, monkeypatch):
    class Owner:
        closed = 0
        async def aclose(self):
            self.closed += 1
    owner = Owner()
    monkeypatch.setattr('argus_app.model_provider.discovery_fallback.configured_discovery_fallback',
        lambda *args: (object(), owner))
    async def fail_start(self):
        raise RuntimeError('synthetic startup failure')
    monkeypatch.setattr('argus_app.application.PluginRuntimeHost.start', fail_start)
    config = ArgusConfig(channel_naming_enabled=False, database_url=SecretStr(database_url), data_dir=tmp_path,
        agent_token=SecretStr('agent-test'), gateway_token=SecretStr('gateway-test'), auto_scan=False,
        discovery_fallback_enabled=True, discovery_fallback_model='perception')
    app = create_app(config)
    with pytest.raises(RuntimeError, match='synthetic startup failure'):
        async with app.router.lifespan_context(app):
            pass
    assert owner.closed == 1
    assert app.state.ready is False
