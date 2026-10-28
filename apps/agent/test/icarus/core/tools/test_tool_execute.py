import asyncio

from icarus.core.hooks import HookDispatcher, HookRegistry
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
