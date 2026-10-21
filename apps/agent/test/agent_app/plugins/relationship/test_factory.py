import asyncio
import json
from pathlib import Path

import httpx
import pytest

from .helpers import Backend


ROOT = Path("apps/agent/src/agent_app/plugins/relationship")


def test_factory_defaults_and_facilities_do_not_request_service(tmp_path, monkeypatch):
    from agent_app.plugins.relationship.factory import create_plugin
    def forbidden(*args, **kwargs):
        raise AssertionError("factory/start must not request service")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    registration = create_plugin("relationship", tmp_path, "session", {"runtime": object(), "identity": object(), "task_channels": object(), "_user_config": {}}, {}, None)
    plugin = registration.plugin
    assert plugin.backend.client.base_url == "http://127.0.0.1:8767"
    asyncio.run(plugin.start())
    asyncio.run(plugin.stop())
    asyncio.run(plugin.stop())
    assert plugin.backend.client.is_closed


@pytest.mark.parametrize("config", [
    {"unknown": 1}, {"timeout_seconds": True}, {"timeout_seconds": "10"},
    {"timeout_seconds": 0}, {"timeout_seconds": 61}, {"endpoint": "relative"},
    {"endpoint": "ftp://localhost"}, {"endpoint": "http://user:secret@localhost"},
    {"endpoint": "http://remote.example"}, {"endpoint": "http://localhost?token=secret"},
])
def test_factory_rejects_invalid_configuration(tmp_path, monkeypatch, config):
    from agent_app.plugins.relationship.factory import create_plugin
    monkeypatch.setenv("RELA_MEM_API_TOKEN", "token")
    with pytest.raises(ValueError):
        create_plugin("relationship", tmp_path, "s", config, {}, None)


def test_remote_requires_token(tmp_path, monkeypatch):
    from agent_app.plugins.relationship.factory import create_plugin
    monkeypatch.delenv("RELA_MEM_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="RELA_MEM_API_TOKEN"):
        create_plugin("relationship", tmp_path, "s", {"endpoint": "https://remote.example"}, {}, None)


def test_manifest_tools_match_factory_and_no_events(tmp_path):
    from agent_app.plugins.relationship.factory import create_plugin
    registration = create_plugin("relationship", tmp_path, "s", {}, {}, None)
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["provided_tools"] == [tool.definition.name for tool in registration.plugin.build_tools()]
    assert len(manifest["provided_tools"]) == 5
    assert manifest["published_events"] == manifest["consumed_events"] == []
    assert manifest["required_config"] == []
    assert manifest["tier"] == "core"
    asyncio.run(registration.plugin.stop())


def test_static_prompt_no_regions_and_close_before_start():
    from agent_app.plugins.relationship.plugin import RelationshipPlugin
    from icarus.core.events import Event
    backend = Backend()
    plugin = RelationshipPlugin("relationship", backend)
    assert not plugin.accepts_event("test", Event())
    section, = plugin.static_prompt_sections()
    assert section.layer == "session"
    assert "memory_remember" in section.content
    assert "confirm_create" in section.content
    assert "relationship_relate" in section.content
    assert "凭据" in section.content
    asyncio.run(plugin.stop())
    assert backend.closed


def test_actual_session_registers_optional_tools_and_static_guidance(tmp_path, monkeypatch):
    from agent_app.application.session_runtime import SessionRuntime
    from icarus.core.identity import SessionIdentity
    from apps.agent.test.agent_app.application.test_session_runtime import make_config
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr("agent_app.plugins.relationship.rela_mem_http_adapter.httpx.Client.send", unavailable)
    async def run():
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["relationship"] = {}
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "relationship"), config=config, publish_update=lambda update: asyncio.sleep(0))
        await runtime.start()
        try:
            plugin = runtime.runtime_host.get_plugin("relationship")
            assert runtime.is_running
            assert "relationship" not in config.runtime.required_plugin_ids
            assert len(plugin.build_tools()) == 5
            prompt = runtime.runtime_host.get_plugin("prompt")._system_prompt
            assert "relationship_find" in prompt
            assert "人物档案" in prompt
            board = runtime.runtime_host.get_plugin("blackboard")
            assert "relationship" not in {definition.region for definition in board.region_registry.definitions()}
            result = plugin.build_tools()[0].invoke({"query": "张三"})
            assert not result.success
            assert "offline" not in result.error
            assert runtime.is_running
        finally:
            await runtime.stop("test")
    asyncio.run(run())


def test_memory_guidance_and_relationship_guidance_split_and_fallback():
    from agent_app.plugins.memory.prompts import MEMORY_GUIDANCE
    from agent_app.plugins.relationship.prompts import RELATIONSHIP_GUIDANCE
    assert "relationship" in MEMORY_GUIDANCE
    assert "未启用" in MEMORY_GUIDANCE
    assert "relationship_remember" in RELATIONSHIP_GUIDANCE
    assert "relationship_relate" in RELATIONSHIP_GUIDANCE
