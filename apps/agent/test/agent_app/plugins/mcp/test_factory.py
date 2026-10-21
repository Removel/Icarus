import asyncio
import json
from pathlib import Path

from agent_app.plugins.mcp.factory import create_plugin
from agent_app.plugins.persistence import (
    PersistenceRuntime,
    SessionIdentity,
)


class ManagerStub:
    async def close(self):
        pass


def test_factory注册内容与manifest一致(tmp_path):
    workspace = tmp_path / "workspace"
    persistence = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session")
    registration = create_plugin(
        "mcp",
        workspace,
        identity.session_id,
        {**({
            "servers": {"browser": {"command": "browser-mcp"}},
            "client_manager": ManagerStub(),
        }), **{k: v for (owner, cap), v in {
            ("persistence", "runtime"): persistence,
            ("persistence", "session"): identity,
            ("persistence", "redactor"): persistence.redactor,
        }.items() for k in (["runtime"] if cap == "runtime" else ["identity"] if cap == "session" else ["task_channels"] if cap == "task_channels" else [])}},
        {},
        None,
    )
    manifest = json.loads(
        Path(
            "apps/agent/src/agent_app/plugins/mcp/manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert [tool.definition.name for tool in registration.plugin.build_tools()] == manifest[
        "provided_tools"
    ]
    assert registration.capabilities == ()
    assert registration.state_provider is None
    asyncio.run(registration.plugin.start())
    assert registration.plugin.bridge.is_running is False
    asyncio.run(registration.plugin.stop())


def test_factory无server配置时仍提供稳定工具入口(tmp_path):
    workspace = tmp_path / "workspace"
    persistence = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session")
    registration = create_plugin(
        "mcp", workspace, identity.session_id, {**({"servers": {}}), **{k: v for (owner, cap), v in {
            ("persistence", "runtime"): persistence,
            ("persistence", "session"): identity,
            ("persistence", "redactor"): persistence.redactor,
        }.items() for k in (["runtime"] if cap == "runtime" else ["identity"] if cap == "session" else ["task_channels"] if cap == "task_channels" else [])}},
        {},
        None,
    )

    assert [tool.definition.name for tool in registration.plugin.build_tools()] == [
        "mcp_tool_list", "mcp_tool_search", "mcp_tool_execute"
    ]
