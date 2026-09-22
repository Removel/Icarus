import asyncio
import os

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistry,
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugins.process import (
    ProcessConfig,
    ProcessPlugin,
    ProcessUpdatedEvent,
)


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires process groups")


def create_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    processes = tmp_path / "processes"
    workspace.mkdir()
    processes.mkdir()
    plugin = ProcessPlugin(
        "process",
        session_id="session",
        workspace_path=workspace,
        processes_dir=processes,
        config=ProcessConfig(terminate_grace_seconds=0.2),
    )
    registry = PluginRegistry()
    registry.register(plugin)
    runtime = PluginRuntime(plugin, registry)
    plugin.bind_background_work_starter(runtime.start_background_work)
    events = []

    async def publish(event):
        events.append(event)

    plugin.bind_publisher(publish)
    return plugin, runtime, events


def test_plugin标准生命周期收束进程并发布stopped(tmp_path):
    async def run():
        plugin, runtime, events = create_runtime(tmp_path)
        await runtime.start()
        started = await plugin.execute("start", command="sleep 30")
        assert runtime.snapshot().background_work_count == 1
        await runtime.quiesce()
        await runtime.drain()
        await runtime.stop(drain=False)
        return plugin, runtime, started, events

    plugin, runtime, started, events = asyncio.run(run())
    assert [event.status for event in events] == ["running", "stopped"]
    assert all(isinstance(event, ProcessUpdatedEvent) for event in events)
    assert runtime.snapshot().background_work_count == 0
    assert plugin._owner_loop is None
    with pytest.raises(ProcessLookupError):
        os.kill(started.pid, 0)


def test_plugin_owner_loop之外的异步调用明确失败(tmp_path):
    plugin, runtime, _ = create_runtime(tmp_path)

    async def start_only():
        await runtime.start()

    asyncio.run(start_only())
    try:
        with pytest.raises(Exception, match="tool_unavailable"):
            asyncio.run(plugin.execute("list"))
    finally:
        # The original loop is closed; no process was started and cleanup is safe.
        asyncio.run(plugin.stop())
