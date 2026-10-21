import asyncio
import json
from pathlib import Path

import pytest

from agent_app.plugins.knowledge.factory import create_plugin

from .helpers import KnowledgeBackendStub


def test_factory注册五个工具且使用默认endpoint(tmp_path, monkeypatch):
    captured = {}
    backend = KnowledgeBackendStub()

    def adapter(endpoint, **kwargs):
        captured.update({"endpoint": endpoint, **kwargs})
        return backend

    monkeypatch.setattr(
        "agent_app.plugins.knowledge.factory.OpenKBHttpAdapter",
        adapter,
    )
    registration = create_plugin(
        "knowledge", tmp_path, "session",
        {**({"knowledge_base": "icarus-project"}), **{k: v for (owner, cap), v in {}.items() for k in (["runtime"] if cap == "runtime" else ["identity"] if cap == "session" else ["task_channels"] if cap == "task_channels" else [])}}, {}, None,
    )
    manifest = json.loads(
        Path("apps/agent/src/agent_app/plugins/knowledge/manifest.json").read_text()
    )
    assert [tool.definition.name for tool in registration.plugin.build_tools()] == manifest["provided_tools"]
    assert captured["endpoint"] == "http://127.0.0.1:7566"
    assert captured["knowledge_base"] == "icarus-project"
    asyncio.run(registration.plugin.stop())
    assert backend.closed is True


@pytest.mark.parametrize(
    "config, message",
    [
        ({}, "knowledge_base"),
        ({"knowledge_base": "kb", "unknown": 1}, "unknown fields"),
        ({"knowledge_base": "kb", "backend": "other"}, "backend"),
        ({"knowledge_base": "kb", "max_file_bytes": True}, "max_file_bytes"),
    ],
)
def test_factory严格校验配置(tmp_path, monkeypatch, config, message):
    monkeypatch.setattr(
        "agent_app.plugins.knowledge.factory.OpenKBHttpAdapter",
        lambda *args, **kwargs: KnowledgeBackendStub(),
    )
    with pytest.raises(ValueError, match=message):
        create_plugin("knowledge", tmp_path, "session", {**(config), **{k: v for (owner, cap), v in {}.items() for k in (["runtime"] if cap == "runtime" else ["identity"] if cap == "session" else ["task_channels"] if cap == "task_channels" else [])}}, {}, None)


def test_factory远端endpoint必须配置token(tmp_path, monkeypatch):
    monkeypatch.delenv("ICARUS_OPENKB_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ICARUS_OPENKB_API_TOKEN"):
        create_plugin(
            "knowledge", tmp_path, "session",
            {**({"knowledge_base": "kb", "endpoint": "https://kb.example.com"}), **{k: v for (owner, cap), v in {}.items() for k in (["runtime"] if cap == "runtime" else ["identity"] if cap == "session" else ["task_channels"] if cap == "task_channels" else [])}},
            {}, None,
        )
