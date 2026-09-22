import asyncio
import os
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
from apps.agent.src.agent_orchestration.tools import (
    ToolExecutionPolicy,
    ToolExecutor,
    ToolResultStore,
)
from apps.agent.src.application.agent_runtime import AgentRuntime
from apps.agent.src.application.session_runtime import SessionRuntime
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
    ToolExecutionSettings,
)
from apps.agent.src.model_provider.types import ToolCall


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires process groups")


def make_config(data_dir, *, process=None):
    model = LLMConfig(
        model_name="test", context_window=128000, max_tokens=1024,
        temperature=0, default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        icarus_data_dir=data_dir,
        runtime={
            "plugin_config": {
                "memory": {"user_id": "test-user", "agent_id": "test-agent"},
                "knowledge": {"knowledge_base": "test-kb"},
                "process": process or {},
            }
        },
        model_settings=ModelSettings(thinking=model, perception=model),
    )


async def wait_process(plugin, process_id, status, timeout=3):
    async with asyncio.timeout(timeout):
        while True:
            snapshot = await plugin.manager.get(process_id)
            if snapshot.status == status:
                return snapshot
            await asyncio.sleep(0.01)


def test_tool_executor执行完整后台进程链路(tmp_path):
    async def run():
        updates = []

        async def publish(update):
            updates.append(update)

        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "process-e2e"),
            config=make_config(tmp_path / "data"),
            publish_update=publish,
        )
        await runtime.start()
        executor = ToolExecutor(
            runtime.tool_registry,
            policy=ToolExecutionPolicy(runtime.config.agent.tool_execution),
        )
        started = await executor.aexecute(
            ToolCall(
                "call-start",
                "background_process",
                {"action": "start", "command": "printf ready; sleep 0.1; exit 7"},
            ),
            task_id="task-1",
        )
        process_id = started.output["process_id"]
        plugin = runtime.runtime_host.get_plugin("process")
        failed = await wait_process(plugin, process_id, "failed")
        logs = await executor.aexecute(
            ToolCall(
                "call-logs", "background_process",
                {"action": "logs", "process_id": process_id},
            ),
            task_id="task-1",
        )
        listed = await executor.aexecute(
            ToolCall("call-list", "background_process", {"action": "list"}),
            task_id="task-1",
        )
        await runtime.plugin_manager.event_bus.drain()
        await runtime.plugin_manager.drain_plugin("runtime-update")
        await runtime.stop("test", timeout=2)
        return started, failed, logs, listed, updates

    started, failed, logs, listed, updates = asyncio.run(run())
    assert started.success is True
    assert started.output["status"] == "running"
    assert failed.exit_code == 7
    assert logs.output["content"] == "ready"
    assert listed.output["processes"][0]["status"] == "failed"
    process_updates = [item for item in updates if item.type == "process.updated"]
    assert [item.payload["status"] for item in process_updates] == [
        "running", "failed"
    ]


def test_logs结果继续受tool预算裁剪并外置(tmp_path):
    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "process-budget"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        settings = ToolExecutionSettings(
            default_timeout_seconds=3.0,
            max_timeout_seconds=3.0,
            default_output_tokens=700,
            min_output_tokens=300,
            max_output_tokens=1000,
            batch_output_tokens=2400,
            max_calls_per_batch=8,
        )
        result_store = ToolResultStore(
            tmp_path / "tool-results", max_file_bytes=1024 * 1024
        )
        executor = ToolExecutor(
            runtime.tool_registry,
            policy=ToolExecutionPolicy(settings),
            result_store=result_store,
        )
        started = await executor.aexecute(
            ToolCall(
                "start", "background_process",
                {"action": "start", "command": "head -c 12000 /dev/zero | tr '\\0' x"},
            ),
            task_id="task-budget",
        )
        plugin = runtime.runtime_host.get_plugin("process")
        await wait_process(plugin, started.output["process_id"], "completed")
        logs = (await executor.aexecute_many(
            [
                ToolCall(
                    "logs", "background_process",
                    {
                        "action": "logs",
                        "process_id": started.output["process_id"],
                        "limit_bytes": 12000,
                    },
                )
            ],
            task_id="task-budget",
        ))[0][1]
        await runtime.stop("test", timeout=2)
        return logs

    logs = asyncio.run(run())
    assert logs.success is True
    assert logs.metadata["result_truncated"] is True
    result_file = Path(logs.metadata["result_file"])
    assert result_file.is_file()
    assert "xxxxxxxx" in result_file.read_text(encoding="utf-8")


def test_real_session显式unload按顺序停止后台进程(tmp_path):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        await runtime.start()
        subscription = runtime.subscribe_updates()
        session_id = await runtime.create_session(tmp_path, "process-unload")
        entry = next(iter(runtime._entries.values()))
        tool = entry.runtime.tool_registry.get("background_process")
        started = await tool.ainvoke({"action": "start", "command": "sleep 30"})
        pid = started.output["pid"]
        async with asyncio.timeout(2):
            while True:
                status = await runtime.get_session_status(tmp_path, session_id)
                if status.background_work_count == 1:
                    break
                await asyncio.sleep(0.01)
        unloaded = await runtime.unload_session(tmp_path, session_id)
        await runtime._update_queue.join()
        seen = []
        while True:
            try:
                seen.append(await asyncio.wait_for(subscription.next_update(), 0.05))
            except TimeoutError:
                break
        subscription.close()
        await runtime.stop()
        return unloaded, pid, seen

    unloaded, pid, seen = asyncio.run(run())
    assert unloaded.status == "unloaded"
    lifecycle = [
        (item.type, item.payload.get("status"))
        for item in seen
        if item.type in {"session.lifecycle", "process.updated"}
    ]
    unloading = lifecycle.index(("session.lifecycle", "unloading"))
    stopped = lifecycle.index(("process.updated", "stopped"))
    unloaded_index = lifecycle.index(("session.lifecycle", "unloaded"))
    assert unloading < stopped < unloaded_index
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_background_process调用遵循统一execution和batch限制(tmp_path):
    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "process-guard"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        settings = ToolExecutionSettings(
            min_output_tokens=100,
            batch_output_tokens=2000,
            max_calls_per_batch=2,
        )
        executor = ToolExecutor(
            runtime.tool_registry, policy=ToolExecutionPolicy(settings)
        )
        guarded = await executor.aexecute(
            ToolCall(
                "guarded", "background_process",
                {
                    "action": "list",
                    "_execution": {
                        "timeout_seconds": 3,
                        "max_output_tokens": 800,
                    },
                },
            )
        )
        batch = await executor.aexecute_many(
            [
                ToolCall(str(index), "background_process", {"action": "list"})
                for index in range(3)
            ]
        )
        await runtime.stop("test", timeout=2)
        return guarded, batch

    guarded, batch = asyncio.run(run())
    assert guarded.success is True
    assert guarded.metadata["effective_timeout_seconds"] == 3
    assert guarded.metadata["effective_output_tokens"] == 800
    assert [result.success for _, result in batch] == [True, True, False]
    assert batch[-1][1].metadata["disposition"] == "budget_exhausted"
