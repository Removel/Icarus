import asyncio
import os

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistry,
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugins.process import (
    BackgroundProcessTool,
    ProcessConfig,
    ProcessPlugin,
)
from apps.agent.src.agent_orchestration.tools import ToolChecker


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires process groups")


def build(tmp_path):
    workspace = tmp_path / "workspace"
    processes = tmp_path / "processes"
    workspace.mkdir()
    processes.mkdir()
    plugin = ProcessPlugin(
        "process", session_id="session", workspace_path=workspace,
        processes_dir=processes,
        config=ProcessConfig(terminate_grace_seconds=0.2),
    )
    registry = PluginRegistry()
    registry.register(plugin)
    runtime = PluginRuntime(plugin, registry)
    plugin.bind_background_work_starter(runtime.start_background_work)

    async def publish(event):
        pass

    plugin.bind_publisher(publish)
    return plugin, runtime, BackgroundProcessTool(plugin)


def test_tool定义只有一个扁平action入口(tmp_path):
    _, _, tool = build(tmp_path)
    definition = tool.definition
    assert ToolChecker().check(tool).valid is True
    assert definition.name == "background_process"
    assert definition.input_schema["required"] == ["action"]
    assert set(definition.input_schema["properties"]["action"]["enum"]) == {
        "start", "list", "get", "logs", "stop"
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "bad"},
        {"action": "start"},
        {"action": "list", "command": "pwd"},
        {"action": "get", "process_id": "p", "cursor": "x"},
        {"action": "logs", "process_id": "p", "page_size": 1},
        {"action": "stop", "process_id": 1},
    ],
)
def test_tool按action拒绝缺失互斥和错误类型参数(tmp_path, arguments):
    _, _, tool = build(tmp_path)
    result = asyncio.run(tool.ainvoke(arguments))
    assert result.success is False
    assert result.error.startswith("invalid_arguments:")


def test_tool五种action返回稳定结构(tmp_path):
    async def run():
        plugin, runtime, tool = build(tmp_path)
        await runtime.start()
        started = await tool.ainvoke(
            {"action": "start", "command": "printf hello; sleep 30"},
            task_id="task-1",
        )
        process_id = started.output["process_id"]
        got = await tool.ainvoke({"action": "get", "process_id": process_id})
        listed = await tool.ainvoke({"action": "list", "page_size": 1})
        async with asyncio.timeout(2):
            while True:
                logs = await tool.ainvoke(
                    {"action": "logs", "process_id": process_id}
                )
                if logs.output["content"]:
                    break
                await asyncio.sleep(0.01)
        stopped = await tool.ainvoke({"action": "stop", "process_id": process_id})
        await runtime.stop()
        return started, got, listed, logs, stopped

    started, got, listed, logs, stopped = asyncio.run(run())
    assert started.success and started.output["status"] == "running"
    assert started.output["origin_task_id"] == "task-1"
    assert got.success and got.output["process_id"] == started.output["process_id"]
    assert listed.success and listed.output["processes"][0] == got.output
    assert logs.success and logs.output["content"] == "hello"
    assert stopped.success and stopped.output["status"] == "stopped"


def test_tool同步入口在owner_loop线程重入时快速失败(tmp_path):
    async def run():
        _, runtime, tool = build(tmp_path)
        await runtime.start()
        result = tool.invoke({"action": "list"}, timeout_seconds=0.1)
        await runtime.stop()
        return result

    result = asyncio.run(run())
    assert result.success is False
    assert result.error.startswith("tool_unavailable:")


def test_tool同步入口从工作线程桥接到owner_loop(tmp_path):
    async def run():
        _, runtime, tool = build(tmp_path)
        await runtime.start()
        result = await asyncio.to_thread(
            tool.invoke, {"action": "list"}, timeout_seconds=1
        )
        await runtime.stop()
        return result

    result = asyncio.run(run())
    assert result.success is True
    assert result.output["processes"] == []
