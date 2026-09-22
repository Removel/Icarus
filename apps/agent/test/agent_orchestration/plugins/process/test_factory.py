import asyncio
import json
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.process.factory import create_plugin


def test_factory注册内容与manifest一致(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session")
    registration = create_plugin(
        "process",
        workspace,
        identity.session_id,
        {},
        {
            ("persistence", "runtime"): runtime,
            ("persistence", "session"): identity,
        },
        None,
    )
    manifest = json.loads(
        Path(
            "apps/agent/src/agent_orchestration/plugins/process/manifest.json"
        ).read_text(encoding="utf-8")
    )

    assert [tool.definition.name for tool in registration.tools] == manifest[
        "provided_tools"
    ]
    assert registration.capabilities == ()
    assert registration.state_provider is None
    assert len(manifest["provided_tools"]) == 1
    assert manifest["provided_capabilities"] == []
    assert manifest["state_scopes"] == []
    assert runtime.resolver.processes_dir(identity).is_dir()
    asyncio.run(registration.plugin.stop())


def test_factory严格校验依赖与配置(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = PersistenceRuntime(tmp_path / "data", workspace)
    identity = SessionIdentity.create(workspace, "session")

    with pytest.raises(ValueError, match="persistence runtime"):
        create_plugin("process", workspace, "session", {}, {}, None)
    with pytest.raises(ValueError, match="unknown"):
        create_plugin(
            "process", workspace, "session", {"unknown": 1},
            {
                ("persistence", "runtime"): runtime,
                ("persistence", "session"): identity,
            }, None,
        )
