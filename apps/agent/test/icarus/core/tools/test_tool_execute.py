import asyncio
from dataclasses import replace

from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry
from icarus.core.hooks.wrappers import ObservableToolExecutor
from icarus.core.models.messages import ToolCall, ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult, ToolExecutor, ToolRegistry
from icarus.core.tools.tool_execute import ToolExecuteTool

from apps.agent.test.icarus.recording_support import CaptureRecords


class EchoTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition(
            "echo",
            "Echo a string value. Example: {'value': 'hello'}.",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments):
        return ToolExecutionResult(
            True,
            {"echo": arguments["value"]},
            metadata={"target": "echo"},
        )


class RecordingExecutor(ToolExecutor):
    def __init__(self, registry):
        super().__init__(registry)
        self.calls = []

    def execute(self, tool_call, **execution):
        self.calls.append(tool_call)
        return super().execute(tool_call, **execution)

    async def aexecute(self, tool_call, **execution):
        self.calls.append(tool_call)
        return await super().aexecute(tool_call, **execution)


def make_tools():
    registry = ToolRegistry()
    registry.register(EchoTool())
    tool = ToolExecuteTool(registry)
    executor = RecordingExecutor(registry)
    return tool, executor


def test_tool_execute分发记录保留身份且隔离参数():
    tool, executor = make_tools()
    for asynchronous in (False, True):
        kwargs = dict(loaded_tools=["echo"], tool_executor=executor,
                      tool_call_id="dispatch-1", execution_control={"max_output_tokens": 512})
        args = {"name": "echo", "arguments": {"value": "hello"}}
        result = (asyncio.run(tool.ainvoke(args, **kwargs)) if asynchronous
                  else tool.invoke(args, **kwargs))
        assert result.metadata["dispatched_tool"] == {
            "id": "dispatch-1", "name": "echo", "arguments": {"value": "hello"},
        }
        assert result.metadata["target"] == "echo"
        args["arguments"]["value"] = "changed"
        assert result.metadata["dispatched_tool"]["arguments"]["value"] == "hello"
        assert "dispatched_tool" not in str(result.as_dict())
    denied = tool.invoke({"name": "echo", "arguments": {"value": "hello"}})
    assert "dispatched_tool" not in denied.metadata


def test_tool_execute目标失败仍记录分发但异常不伪造完成():
    class FailedExecutor(RecordingExecutor):
        def execute(self, call, **execution):
            return ToolExecutionResult(False, error="target rejected", metadata={"original": 1})
    tool, executor = make_tools()
    failed = tool.invoke({"name": "echo", "arguments": {"value": "hello"}},
                         loaded_tools=["echo"], tool_executor=FailedExecutor(executor.registry),
                         tool_call_id="failure")
    assert failed.success is False
    assert failed.error == "target rejected"
    assert failed.metadata["original"] == 1
    assert failed.metadata["dispatched_tool"]["id"] == "failure"


def test_tool_execute记录分发参数不是Hook后实参():
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(ToolExecuteTool(registry))
    class Rewrite(BaseHook):
        def handle(self, event, value):
            if event.phase == "before":
                return replace(value, arguments={"value": "rewritten"})
            return value
    hooks = HookRegistry()
    hooks.register("tool.execute", Rewrite(), priority=1)
    executor = ObservableToolExecutor(ToolExecutor(registry), HookDispatcher(hooks))
    result = executor.execute(ToolCall("outer", "tool_execute", {
        "name": "echo", "arguments": {"value": "original"},
    }), loaded_tools=["echo"], tool_executor=executor)
    assert result.output == {"echo": "rewritten"}
    assert result.metadata["dispatched_tool"]["arguments"] == {"value": "original"}


def test_tool_execute未加载时硬门拒绝并引导搜索():
    tool, executor = make_tools()

    result = tool.invoke(
        {"name": "echo", "arguments": {"value": "hello"}},
        loaded_tools=[],
        tool_executor=executor,
    )

    assert result.success is False
    assert "echo" in result.error
    assert "tool_search('<scope>')" in result.error
    assert executor.calls == []


def test_tool_execute已加载后以目标身份执行并保留metadata():
    tool, executor = make_tools()

    result = tool.invoke(
        {"name": "echo", "arguments": {"value": "hello"}},
        loaded_tools=["echo"],
        tool_executor=executor,
        tool_call_id="call-1",
        task_id="task-1",
        run_id="run-1",
        step=2,
    )

    assert result.success is True
    assert result.output == {"echo": "hello"}
    assert result.metadata["target"] == "echo"
    assert len(executor.calls) == 1
    assert executor.calls[0].id == "call-1"
    assert executor.calls[0].name == "echo"
    assert executor.calls[0].arguments == {"value": "hello"}


def test_tool_execute异步与同步一致且late_bind后可执行():
    registry = ToolRegistry()
    registry.register(EchoTool())
    tool = ToolExecuteTool()
    tool.bind_registry(registry)
    executor = RecordingExecutor(registry)

    synced = tool.invoke(
        {"name": "echo", "arguments": {"value": "hello"}},
        loaded_tools=["echo"],
        tool_executor=executor,
    )
    asynced = asyncio.run(
        tool.ainvoke(
            {"name": "echo", "arguments": {"value": "hello"}},
            loaded_tools=["echo"],
            tool_executor=executor,
        )
    )

    assert asynced.success == synced.success
    assert asynced.output == synced.output
    assert [call.name for call in executor.calls] == ["echo", "echo"]
    assert tool.can_run_parallel({"name": "echo", "arguments": {}}) is True


def test_tool_execute校验分发参数且arguments必须是object():
    tool, executor = make_tools()

    unknown = tool.invoke({"name": "echo", "extra": {}})
    invalid_name = tool.invoke({"name": "", "arguments": {}})
    invalid_arguments = tool.invoke({"name": "echo", "arguments": "{}"})

    assert unknown.success is False
    assert "unknown arguments: extra" in unknown.error
    assert invalid_name.success is False
    assert "name must be a non-empty string" in invalid_name.error
    assert invalid_arguments.success is False
    assert "arguments must be an object" in invalid_arguments.error
    assert executor.calls == []
    assert tool.definition.input_schema["properties"]["arguments"]["type"] == "object"
    assert tool.definition.input_schema["required"] == ["name"]


def test_tool_execute_schema错误包含字段期望和实际值且不执行():
    tool, executor = make_tools()

    wrong_type = tool.invoke(
        {"name": "echo", "arguments": {"value": 3}},
        loaded_tools=["echo"],
        tool_executor=executor,
    )
    missing = tool.invoke(
        {"name": "echo", "arguments": {}},
        loaded_tools=["echo"],
        tool_executor=executor,
    )

    assert wrong_type.success is False
    assert wrong_type.output["errors"][0]["path"] == "$.value"
    assert wrong_type.output["errors"][0]["expected"] == "string"
    assert wrong_type.output["errors"][0]["actual"] == 3
    assert "failed type validation" in wrong_type.error
    assert missing.success is False
    assert missing.output["errors"][0]["path"] == "$"
    assert missing.output["errors"][0]["expected"] == ["value"]
    assert executor.calls == []


def test_tool_execute默认使用绑定registry的executor():
    tool, _ = make_tools()

    result = tool.invoke(
        {"name": "echo", "arguments": {"value": "hello"}},
        loaded_tools=["echo"],
    )

    assert result.success is True
    assert result.output == {"echo": "hello"}


def test_tool_execute_observable链路只看到目标工具身份():
    registry = ToolRegistry()
    registry.register(EchoTool())
    dispatch = ToolExecuteTool(registry)
    registry.register(dispatch)
    hooks = HookRegistry()
    recorder = CaptureRecords()
    hooks.recorder = recorder
    full_executor = ObservableToolExecutor(
        ToolExecutor(registry), HookDispatcher(hooks)
    )
    snapshot = full_executor.snapshot(["tool_execute"])

    result = snapshot.execute(
        ToolCall(
            "call-1",
            "tool_execute",
            {"name": "echo", "arguments": {"value": "hello"}},
        ),
        loaded_tools=["echo"],
        tool_executor=full_executor,
    )

    assert result.success is True
    observed = [
        event.data["tool_call"]["name"]
        for event in recorder.events
        if event.name == "tool.execute" and "tool_call" in event.data
    ]
    assert observed == ["echo", "echo"]
