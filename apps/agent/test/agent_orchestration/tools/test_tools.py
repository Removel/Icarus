import asyncio
import json
from pathlib import Path
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
from apps.agent.src.agent_orchestration.tools.execution_policy import (
    ToolContextBudgetExceededError,
    ToolExecutionPolicy,
)
from apps.agent.src.agent_orchestration.tools.result_budget import (
    conservative_token_count,
    serialize_tool_result,
)
from apps.agent.src.agent_orchestration.tools.result_store import ToolResultStore
from apps.agent.src.model_config import ToolExecutionSettings
from apps.agent.src.model_provider.types import ToolCall, ToolDefinition
from apps.agent.src.model_provider.types import ImagePart


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


def test_tool_checker拒绝非对象properties避免execution注入崩溃():
    class BrokenSchemaTool(EchoTool):
        @property
        def definition(self):
            return ToolDefinition(
                "broken",
                "broken schema",
                {"type": "object", "properties": []},
            )

    result = ToolChecker().check(BrokenSchemaTool())

    assert result.valid is False
    assert "properties must be an object" in result.errors[0]


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


def test_tool_executor自动注入execution且不传给业务tool():
    registry = ToolRegistry()
    registry.register(EchoTool())
    executor = ToolExecutor(registry)

    definition = executor.definitions()[0]
    result = executor.execute(
        ToolCall(
            "call-1",
            "echo",
            {
                "value": "ok",
                "_execution": {
                    "timeout_seconds": 30,
                    "max_output_tokens": 1000,
                },
            },
        )
    )

    assert "_execution" in definition.input_schema["properties"]
    assert "_execution" not in registry.definitions()[0].input_schema["properties"]
    assert result.output == "ok"
    assert result.metadata["effective_timeout_seconds"] == 30
    assert result.metadata["effective_output_tokens"] == 1000


def test_tool_executor非法execution不启动业务tool():
    called = False

    class GuardedTool(EchoTool):
        def invoke(self, arguments):
            nonlocal called
            called = True
            return super().invoke(arguments)

    registry = ToolRegistry()
    registry.register(GuardedTool())
    result = ToolExecutor(registry).execute(
        ToolCall(
            "call-1",
            "echo",
            {"value": "ok", "_execution": {"max_output_tokens": 1}},
        )
    )

    assert called is False
    assert result.success is False
    assert result.metadata["disposition"] == "invalid_execution_control"


def test_tool_execution_result只在有图片时序列化asset引用():
    plain = ToolExecutionResult(success=True, output="ok")
    image = ToolExecutionResult(
        success=True,
        output="captured",
        images=(ImagePart("assets/a.png", "asset", "image/png"),),
    )

    assert plain.as_dict() == {
        "success": True, "output": "ok", "error": None
    }
    assert image.as_dict()["images"] == [
        {
            "source": "assets/a.png",
            "source_type": "asset",
            "media_type": "image/png",
        }
    ]


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


def test_tool_executor_batch超限外置并确保所有结果在预算内(tmp_path):
    settings = ToolExecutionSettings(
        default_output_tokens=700,
        min_output_tokens=300,
        max_output_tokens=1000,
        batch_output_tokens=1000,
        max_calls_per_batch=3,
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    executor = ToolExecutor(
        registry,
        policy=ToolExecutionPolicy(settings),
        result_store=ToolResultStore(
            tmp_path / "tool-results", max_file_bytes=1024 * 1024
        ),
    )
    calls = [
        ToolCall(f"call-{index}", "echo", {"value": str(index) * 900})
        for index in range(3)
    ]

    results = executor.execute_many(calls, task_id="task-1")

    visible = [
        conservative_token_count(serialize_tool_result(result))
        for _, result in results
    ]
    assert sum(visible) <= 1000
    assert all(result.metadata["result_truncated"] for _, result in results)
    assert all(result.metadata["result_file_complete"] for _, result in results)
    assert all(
        result.metadata["result_file"].startswith(
            str(tmp_path / "tool-results" / "task-1")
        )
        for _, result in results
    )
    stored = json.loads(
        Path(results[0][1].metadata["result_file"]).read_text(encoding="utf-8")
    )
    assert stored["output"] == "0" * 900


def test_tool_executor超过调用上限只执行前几个并闭合全部结果():
    settings = ToolExecutionSettings(
        min_output_tokens=100,
        max_calls_per_batch=2,
        batch_output_tokens=1000,
    )
    calls_seen = []

    class RecordingTool(EchoTool):
        def invoke(self, arguments):
            calls_seen.append(arguments["value"])
            return super().invoke(arguments)

    registry = ToolRegistry()
    registry.register(RecordingTool())
    executor = ToolExecutor(registry, policy=ToolExecutionPolicy(settings))
    calls = [
        ToolCall(f"call-{index}", "echo", {"value": index})
        for index in range(4)
    ]

    results = executor.execute_many(calls)

    assert calls_seen == [0, 1]
    assert [call.id for call, _ in results] == [
        "call-0", "call-1", "call-2", "call-3"
    ]
    assert [result.success for _, result in results] == [True, True, False, False]
    assert results[-1][1].metadata["disposition"] == "budget_exhausted"


def test_tool_executor最小结果预算无法闭合时拒绝整个group():
    settings = ToolExecutionSettings(
        min_output_tokens=400,
        max_calls_per_batch=2,
        batch_output_tokens=800,
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    executor = ToolExecutor(
        registry,
        policy=ToolExecutionPolicy(settings, context_window=1000),
    )

    with pytest.raises(ToolContextBudgetExceededError):
        executor.execute_many(
            [ToolCall("call-1", "echo", {"value": "ok"})]
        )


def test_tool_executor异步tool超时取消并返回配对错误():
    cancelled = asyncio.Event()

    class AsyncTool(EchoTool):
        async def ainvoke(self, arguments):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    settings = ToolExecutionSettings(default_timeout_seconds=1)
    registry = ToolRegistry()
    registry.register(AsyncTool())
    executor = ToolExecutor(registry, policy=ToolExecutionPolicy(settings))

    result = asyncio.run(
        executor.aexecute(ToolCall("call-1", "echo", {"value": "ok"}))
    )

    assert result.success is False
    assert result.metadata["disposition"] == "timed_out"
    assert result.metadata["cancellation_confirmed"] is True
    assert cancelled.is_set()
