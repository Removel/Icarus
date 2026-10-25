import asyncio
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from icarus.core.tools import (
    BaseTool,
    ToolChecker,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from icarus.core.tools.execution_policy import (
    ToolContextBudgetExceededError,
    ToolExecutionPolicy,
)
from icarus.core.tools.result_budget import (
    conservative_token_count,
    serialize_tool_result,
)
from icarus.core.tools.result_store import ToolResultStore
from agent_app.config import ToolExecutionSettings
from icarus.core.models.messages import ToolCall, ToolDefinition
from icarus.core.models.messages import ImagePart


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


@pytest.mark.parametrize("async_mode", [False, True])
def test_failed_result_saved_raw_before_short_disclosure(tmp_path, async_mode):
    diagnostic = "Traceback (most recent call last):\n" + '  file "local.py", line 42\n' * 40 + "ValueError: important final diagnostic\n"
    original = ToolExecutionResult(
        False, {"exit_code": 7, "stdout": "created output.txt before failure\n", "stderr": diagnostic}, diagnostic
    )

    class FailingTool(EchoTool):
        def invoke(self, arguments):
            return original

    registry = ToolRegistry()
    registry.register(FailingTool())
    executor = ToolExecutor(
        registry, result_store=ToolResultStore(tmp_path / "results", max_file_bytes=16 * 1024 * 1024)
    )
    calls = [ToolCall("failed-call", "echo", {})]
    pairs = asyncio.run(executor.aexecute_many(calls, task_id="task-1")) if async_mode else executor.execute_many(calls, task_id="task-1")
    result = pairs[0][1]
    text = serialize_tool_result(result)
    assert conservative_token_count(text) <= 1000
    assert result.success is False
    assert result.output["exit_code"] == 7
    assert "created output.txt" in result.output["stdout"]
    assert "ValueError: important final diagnostic" in result.error
    assert len(result.error) < 400
    assert diagnostic not in text
    saved_path = Path(result.metadata["result_file"])
    assert str(saved_path) in text
    assert json.loads(saved_path.read_text(encoding="utf-8")) == original.as_dict()
    assert "content omitted" not in saved_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("async_mode", [False, True])
def test_fitting_image_failure_survives_preferred_short_target(tmp_path, async_mode):
    image = ImagePart(
        "https://example.invalid/image.png?sig=" + "A" * 1200,
        "url", "image/png",
    )
    original = ToolExecutionResult(
        False,
        {"exit_code": 7, "stdout": "created file before failure\n"},
        "permission denied",
        images=(image,),
    )

    class ImageFailureTool(EchoTool):
        def invoke(self, arguments):
            return original

    registry = ToolRegistry()
    registry.register(ImageFailureTool())
    executor = ToolExecutor(
        registry,
        result_store=ToolResultStore(tmp_path / "results", max_file_bytes=100000),
    )
    calls = [ToolCall("image-failure", "echo", {})]
    pairs = (
        asyncio.run(executor.aexecute_many(calls, task_id="task-1"))
        if async_mode else executor.execute_many(calls, task_id="task-1")
    )
    result = pairs[0][1]
    assert result.success is False
    assert result.error == original.error
    assert result.output == original.output
    assert result.images == (image,)
    assert conservative_token_count(serialize_tool_result(result)) <= 4000
    saved_path = next((tmp_path / "results" / "task-1").glob("*.txt"))
    assert json.loads(saved_path.read_text(encoding="utf-8")) == original.as_dict()


@pytest.mark.parametrize("storage", ["unavailable", "fails", "incomplete"])
def test_failed_result_storage_fallback_keeps_requested_preview(tmp_path, storage):
    diagnostic = "start evidence\n" + "detail evidence\n" * 300 + "final evidence\n"
    raw = ToolExecutionResult(False, {"exit_code": 9, "stdout": "wrote partial data\n", "stderr": diagnostic}, diagnostic)
    store = None
    if storage != "unavailable":
        root = tmp_path / "results"
        if storage == "fails":
            root.write_text("not a directory", encoding="utf-8")
        store = ToolResultStore(root, max_file_bytes=128 if storage == "incomplete" else 100000)
    settings = ToolExecutionSettings(default_output_tokens=3000)
    executor = ToolExecutor(ToolRegistry(), policy=ToolExecutionPolicy(settings), result_store=store)
    call = ToolCall("failure", "unused", {})
    result = executor.finalize_group([call], {call.id: raw}, task_id="task-1")[call.id]
    text = serialize_tool_result(result)
    assert 1000 < conservative_token_count(text) <= 3000
    assert result.output["exit_code"] == 9
    assert "wrote partial data" in result.output["stdout"]
    assert "start evidence" in text and "final evidence" in text
    assert "full Tool Result:" not in text
    if storage == "incomplete":
        assert result.metadata["result_file_complete"] is False
        assert "incomplete" in text
    else:
        assert "result_file" not in result.metadata
        assert "not saved" in text
        assert str(tmp_path / "results") not in text


def test_small_failures_and_repeated_read_do_not_create_result_files(tmp_path):
    from agent_app.plugins.builtin_tools import ReadTool

    path = tmp_path / "page.txt"
    path.write_text("short page\n", encoding="utf-8")
    root = tmp_path / "results"
    registry = ToolRegistry()
    registry.register(ReadTool())
    executor = ToolExecutor(registry, result_store=ToolResultStore(root, max_file_bytes=100000))
    for _ in range(2):
        result = executor.execute_many([ToolCall("read", "read", {"path": str(path)})], task_id="task-1")[0][1]
        assert result.output["content"] == "short page\n"
    failed = ToolCall("missing", "missing", {})
    assert executor.execute_many([failed], task_id="task-1")[0][1].success is False
    assert not root.exists()


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
