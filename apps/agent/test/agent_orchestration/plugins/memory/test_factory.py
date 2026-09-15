import asyncio
import json
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.blackboard import RegionRegistry
from apps.agent.src.agent_orchestration.plugins.memory.factory import create_plugin
from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity

from .test_plugin import BackendStub


def dependencies(tmp_path):
    return {
        ("persistence", "session"): SessionIdentity.create(tmp_path, "session"),
        ("blackboard", "region_registry"): RegionRegistry(),
    }


def test_factory注册memory_region和八个工具(tmp_path, monkeypatch):
    deps = dependencies(tmp_path)
    backend = BackendStub()
    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.memory.factory.Mem0HttpAdapter",
        lambda *args, **kwargs: backend,
    )
    registration = create_plugin(
        "memory", tmp_path, "session",
        {
            "user_id": "u", "agent_id": "a",
            "recall": {"top_k": 5, "max_context_chars": 7000},
        },
        deps, None,
    )
    manifest = json.loads(Path(
        "apps/agent/src/agent_orchestration/plugins/memory/manifest.json"
    ).read_text())
    assert [tool.definition.name for tool in registration.tools] == manifest["provided_tools"]
    definition = deps[("blackboard", "region_registry")].get("memory")
    assert definition.required_for_start is True
    assert definition.owner_plugin_id == "memory"
    assert definition.max_refs == 5
    assert definition.max_data_chars == 7000
    asyncio.run(registration.plugin.stop())
    assert backend.closed is True
    assert deps[("blackboard", "region_registry")].definitions() == ()


@pytest.mark.parametrize(
    "config_value, expected",
    [(None, True), (False, False)],
)
def test_factory传递输入语言保持配置(
    tmp_path, monkeypatch, config_value, expected,
):
    captured = {}
    backend = BackendStub()

    def create_backend(*args, **kwargs):
        captured.update(kwargs)
        return backend

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.memory.factory.Mem0HttpAdapter",
        create_backend,
    )
    config = {"user_id": "u", "agent_id": "a"}
    if config_value is not None:
        config["preserve_input_language"] = config_value
    registration = create_plugin(
        "memory", tmp_path, "session", config,
        dependencies(tmp_path), None,
    )

    assert captured["preserve_input_language"] is expected

    asyncio.run(registration.plugin.stop())


def test_factory显式工具超时不受自动召回deadline影响(tmp_path, monkeypatch):
    captured = {}
    backend = BackendStub()

    def create_backend(*args, **kwargs):
        captured.update(kwargs)
        return backend

    monkeypatch.setattr(
        "apps.agent.src.agent_orchestration.plugins.memory.factory.Mem0HttpAdapter",
        create_backend,
    )
    registration = create_plugin(
        "memory", tmp_path, "session",
        {
            "user_id": "u", "agent_id": "a",
            "recall": {"deadline_ms": 50},
        },
        dependencies(tmp_path), None,
    )

    assert captured["timeout_seconds"] == 60

    asyncio.run(registration.plugin.stop())


@pytest.mark.parametrize(
    "config, message",
    [
        ({"agent_id": "a"}, "user_id"),
        ({"user_id": "u"}, "agent_id"),
        ({"user_id": "u", "agent_id": "a", "unknown": 1}, "unknown fields"),
        ({"user_id": "u", "agent_id": "a", "preserve_input_language": "yes"}, "preserve_input_language"),
        ({"user_id": "u", "agent_id": "a", "recall": {"deadline_ms": 1001}}, "deadline_ms"),
    ],
)
def test_factory严格校验配置(tmp_path, config, message):
    with pytest.raises(ValueError, match=message):
        create_plugin("memory", tmp_path, "session", config, dependencies(tmp_path), None)
