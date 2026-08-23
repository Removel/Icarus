import asyncio
import threading
import time
from typing import Any

import pytest

from apps.agent.src.agent_orchestration.tools import (
    BaseTool,
    ToolChecker,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from apps.agent.src.model_provider.types import ToolCall, ToolDefinition


class EchoTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="echo",
            description="返回输入",
            input_schema={
                "type": "object",
                "properties": {"value": {}},
            },
        )

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(success=True, output=arguments["value"])


class SlowTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="slow",
            description="短暂等待",
            input_schema={"type": "object"},
        )

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        time.sleep(arguments["delay"])
        return ToolExecutionResult(success=True, output=arguments["value"])

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return arguments.get("parallel", True)


class InvalidResultTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="invalid",
            description="返回错误类型",
            input_schema={"type": "object"},
        )

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return "invalid"  # type: ignore[return-value]


def test_tool_checker和registry_跳过不合规和重复工具(caplog):
    registry = ToolRegistry(ToolChecker())

    assert registry.register(EchoTool()) is True
    assert registry.register(EchoTool()) is False
    assert registry.register(object()) is False  # type: ignore[arg-type]

    assert registry.names() == ["echo"]
    assert registry.definitions()[0].name == "echo"
    assert "duplicate tool" in caplog.text
    assert "invalid tool" in caplog.text


def test_tool_registry_select_不传使用全部且未知工具被忽略(caplog):
    registry = ToolRegistry()
    registry.register(EchoTool())

    assert [tool.definition.name for tool in registry.select()] == ["echo"]
    assert registry.select([]) == []
    assert [tool.definition.name for tool in registry.select(["missing", "echo"])] == [
        "echo"
    ]
    assert "not registered" in caplog.text


def test_tool_executor_统一包装成功未知工具和非法返回():
    registry = ToolRegistry()
    registry.register_many([EchoTool(), InvalidResultTool()])
    executor = ToolExecutor(registry)

    success = executor.execute(ToolCall("call-1", "echo", {"value": "ok"}))
    missing = executor.execute(ToolCall("call-2", "missing", {}))
    invalid = executor.execute(ToolCall("call-3", "invalid", {}))

    assert success == ToolExecutionResult(success=True, output="ok")
    assert missing.success is False
    assert "not registered" in missing.error
    assert invalid.success is False
    assert "invalid result" in invalid.error


def test_tool_executor_同步批量并发且保持原始顺序():
    registry = ToolRegistry()
    registry.register(SlowTool())
    executor = ToolExecutor(registry)
    calls = [
        ToolCall(
            "call-1",
            "slow",
            {"delay": 0.08, "value": 1, "parallel": True},
        ),
        ToolCall(
            "call-2",
            "slow",
            {"delay": 0.08, "value": 2, "parallel": True},
        ),
    ]

    started_at = time.monotonic()
    results = executor.execute_many(calls)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.14
    assert [tool_call.id for tool_call, _ in results] == ["call-1", "call-2"]
    assert [result.output for _, result in results] == [1, 2]


def test_tool_executor_异步批量并发且保持原始顺序():
    registry = ToolRegistry()
    registry.register(SlowTool())
    executor = ToolExecutor(registry)
    calls = [
        ToolCall(
            "call-1",
            "slow",
            {"delay": 0.08, "value": 1, "parallel": True},
        ),
        ToolCall(
            "call-2",
            "slow",
            {"delay": 0.08, "value": 2, "parallel": True},
        ),
    ]

    async def run():
        started_at = time.monotonic()
        results = await executor.aexecute_many(calls)
        return results, time.monotonic() - started_at

    results, elapsed = asyncio.run(run())

    assert elapsed < 0.14
    assert [tool_call.id for tool_call, _ in results] == ["call-1", "call-2"]
    assert [result.output for _, result in results] == [1, 2]


def test_tool_executor_取消后不等待或消费同步tool迟到结果():
    started = threading.Event()
    release = threading.Event()

    class BlockingTool(EchoTool):
        def invoke(self, arguments):
            started.set()
            release.wait(timeout=1)
            return ToolExecutionResult(success=True, output="late")

    async def run():
        registry = ToolRegistry()
        registry.register(BlockingTool())
        executor = ToolExecutor(registry)
        task = asyncio.create_task(
            executor.aexecute_many([ToolCall("call-1", "echo", {})])
        )
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(run())


def test_tool_executor_按照连续可并行调用分批():
    registry = ToolRegistry()
    registry.register(SlowTool())
    registry.register(EchoTool())
    executor = ToolExecutor(registry)
    calls = [
        ToolCall("call-1", "echo", {"value": 1}),
        ToolCall("call-2", "echo", {"value": 2}),
        ToolCall("call-3", "slow", {"value": 3, "parallel": True}),
        ToolCall("call-4", "slow", {"value": 4, "parallel": True}),
        ToolCall("call-5", "echo", {"value": 5}),
        ToolCall("call-6", "slow", {"value": 6, "parallel": True}),
    ]

    batches = executor.build_batches(calls)

    assert [[tool_call.id for tool_call in batch] for batch in batches] == [
        ["call-1"],
        ["call-2"],
        ["call-3", "call-4"],
        ["call-5"],
        ["call-6"],
    ]
