import json
import logging

import pytest

from apps.agent.src.agent_orchestration.hooks import HookDispatcher, HookRegistry
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
)


def test_persistence_runtime_要求环境变量(monkeypatch, tmp_path):
    monkeypatch.delenv("ICARUS_DATA_DIR", raising=False)

    with pytest.raises(RuntimeError, match="ICARUS_DATA_DIR"):
        PersistenceRuntime.from_env(tmp_path)


def test_persistence_runtime_session_scope_写元数据trace和日志(tmp_path):
    runtime = PersistenceRuntime(
        data_dir=tmp_path / "data",
        workspace_path=tmp_path / "workspace",
    )
    registry = HookRegistry()
    logger = logging.getLogger("persistence-test")
    logger.setLevel(logging.INFO)
    runtime.start(registry, logger)
    with runtime.session_scope(
        session_id="session-1",
        task_id="task-1",
    ) as identity:
        dispatcher = HookDispatcher(registry)
        dispatcher.trigger(
            "custom.event",
            "after",
            {"authorization": "secret", "value": "ok"},
        )
        logger.info("session log")
    logger.info("workspace log")
    runtime.stop(drain=True, logger=logger)

    trace_lines = runtime.resolver.trace_file(identity).read_text(
        encoding="utf-8"
    ).splitlines()
    record = json.loads(trace_lines[0])
    assert record["task_id"] == "task-1"
    assert record["data"]["authorization"] == "[REDACTED]"
    assert "session log" in runtime.resolver.session_log(identity).read_text(
        encoding="utf-8"
    )
    assert "workspace log" in runtime.resolver.workspace_log(identity).read_text(
        encoding="utf-8"
    )
    session = json.loads(
        runtime.resolver.session_metadata(identity).read_text(encoding="utf-8")
    )
    assert session["status"] == "closed"


def test_persistence_runtime_重启不重复注册hook(tmp_path):
    runtime = PersistenceRuntime(
        data_dir=tmp_path / "data",
        workspace_path=tmp_path / "workspace",
    )
    registry = HookRegistry()
    logger = logging.getLogger("persistence-restart-test")
    runtime.start(registry, logger)
    runtime.stop(logger=logger)
    runtime.start(registry, logger)
    with runtime.session_scope(
        session_id="session-1",
        task_id="task-1",
    ) as identity:
        HookDispatcher(registry).trigger("custom.event", "after", {"value": 1})
    runtime.stop(logger=logger)

    lines = runtime.resolver.trace_file(identity).read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 1
