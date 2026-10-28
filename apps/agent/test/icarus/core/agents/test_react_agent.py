import asyncio
import json

import pytest

from icarus.core.agents import ReActAgent
from icarus.core.run_control import (
    AgentRunCancelled,
    MaxStepsExceededError,
    TaskChannel,
)
from icarus.core.tools import (
    BaseTool,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from icarus.core.tools.tool_execute import ToolExecuteTool
from icarus.core.tools.tool_search import ToolSearchTool
from icarus.core.tools.tool_surface import ToolSurfaceCatalog
from icarus.core.llm import BaseLLM, InvalidToolCallArgumentsError
from icarus.core.models.messages import (
    ImagePart,
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
    Usage,
)


class QueueLLM(BaseLLM):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ainvoke(self, messages, tools=None):
        return self.invoke(messages, tools)

    def stream(self, messages, tools=None):
        return iter(())

    async def astream(self, messages, tools=None):
        if False:
            yield

    def close(self):
        pass

    async def aclose(self):
        pass


class EchoTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition(
            "echo",
            "返回输入",
            {
                "type": "object",
                "properties": {"value": {}},
            },
        )

    def invoke(self, arguments):
        return ToolExecutionResult(True, {"echo": arguments["value"]})


def make_agent(responses):
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = QueueLLM(responses)
    return ReActAgent("thinking", llm, ToolExecutor(registry)), llm


def tool_response(call_id="call-1", value="hello"):
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[ToolCall(call_id, "echo", {"value": value})],
        ),
        reasoning="需要执行工具",
        usage=Usage(10, 2),
        finish_reason="tool_call",
    )


def final_response(text="done"):
    return LLMResponse(
        message=Message("assistant", [TextPart(text)]),
        reasoning="完成",
        usage=Usage(8, 3),
        finish_reason="stop",
    )


def make_surface_agent(responses, dispatch=None):
    registry = ToolRegistry()
    registry.register(EchoTool())
    catalog = ToolSurfaceCatalog.build([("echo", "alpha")], registry)
    registry.register(ToolSearchTool(catalog))
    dispatch = dispatch or ToolExecuteTool(registry)
    registry.register(dispatch)
    llm = QueueLLM(responses)
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))
    return agent, llm, dispatch


def surface_response(call_id, name, arguments):
    return LLMResponse(
        message=Message(
            "assistant",
            [],
            tool_calls=[ToolCall(call_id, name, arguments)],
        ),
        finish_reason="tool_call",
    )


def run_surface_agent(agent, method):
    arguments = {
        "system_prompt": "",
        "history_messages": [],
        "input_prompt": "hello",
        "tools": ["tool_search", "tool_execute"],
    }
    if method == "invoke":
        return agent.invoke(**arguments)
    return asyncio.run(agent.ainvoke(**arguments))


@pytest.mark.parametrize("method", ["invoke", "ainvoke"])
def test_react_agent搜索后分发执行且定义数组冻结(method):
    agent, llm, _ = make_surface_agent(
        [
            surface_response("search-1", "tool_search", {"query": "alpha"}),
            surface_response(
                "execute-1",
                "tool_execute",
                {"name": "echo", "arguments": {"value": "hello"}},
            ),
            final_response(),
        ]
    )

    result = run_surface_agent(agent, method)

    assert result.message.content == [TextPart("done")]
    assert len(llm.calls) == 3
    for _, definitions in llm.calls:
        assert [definition.name for definition in definitions] == [
            "tool_search",
            "tool_execute",
        ]
    tool_messages = [
        message for message in llm.calls[-1][0] if message.role == "tool"
    ]
    assert json.loads(tool_messages[0].content[0].text)["success"] is True
    assert json.loads(tool_messages[1].content[0].text)["output"] == {
        "echo": "hello"
    }


def test_react_agent未加载工具被硬门拒绝():
    agent, llm, _ = make_surface_agent(
        [
            surface_response(
                "execute-1",
                "tool_execute",
                {"name": "echo", "arguments": {"value": "hello"}},
            ),
            final_response(),
        ]
    )

    run_surface_agent(agent, "invoke")

    tool_message = llm.calls[-1][0][-1]
    result = json.loads(tool_message.content[0].text)
    assert result["success"] is False
    assert "tool_search('<scope>')" in result["error"]


def test_react_agent加载名单不跨run继承():
    agent, llm, _ = make_surface_agent(
        [
            surface_response("search-1", "tool_search", {"query": "alpha"}),
            surface_response(
                "execute-1",
                "tool_execute",
                {"name": "echo", "arguments": {"value": "first"}},
            ),
            final_response("first"),
            surface_response(
                "execute-2",
                "tool_execute",
                {"name": "echo", "arguments": {"value": "second"}},
            ),
            final_response("second"),
        ]
    )

    run_surface_agent(agent, "invoke")
    run_surface_agent(agent, "invoke")

    second_run_tool = llm.calls[-1][0][-1]
    result = json.loads(second_run_tool.content[0].text)
    assert result["success"] is False
    assert "tool_search('<scope>')" in result["error"]


def test_react_agent加载名与可执行全集求交并保序去重():
    class SearchTool(ToolSearchTool):
        def invoke(self, arguments):
            return ToolExecutionResult(
                True,
                "loaded",
                metadata={"tools_loaded": ["echo", "missing", "echo"]},
            )

    class RecordingExecuteTool(ToolExecuteTool):
        def __init__(self):
            super().__init__()
            self.loaded = []

        def invoke(self, arguments, **execution):
            self.loaded.append(list(execution["loaded_tools"]))
            return super().invoke(arguments, **execution)

    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(SearchTool())
    dispatch = RecordingExecuteTool()
    dispatch.bind_registry(registry)
    registry.register(dispatch)
    llm = QueueLLM(
        [
            surface_response("search-1", "tool_search", {"query": "alpha"}),
            surface_response(
                "execute-1",
                "tool_execute",
                {"name": "echo", "arguments": {"value": "hello"}},
            ),
            final_response(),
        ]
    )
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    agent.invoke("", [], "hello", tools=["tool_search", "tool_execute"])

    assert dispatch.loaded == [["echo"]]


def test_react_agent_完成toolcall回填并继续对话():
    agent, llm = make_agent([tool_response(), final_response()])

    result = agent.invoke(
        system_prompt="你是助手",
        history_messages=[Message("user", [TextPart("history")])],
        input_prompt="处理图片",
        input_images=[ImagePart("https://example.com/image.png")],
        tools=["echo"],
    )

    assert result.message.content == [TextPart("done")]
    assert result.finish_reason == "stop"
    assert result.steps == 2
    assert result.reasoning == "需要执行工具\n完成"
    assert result.usage == Usage(18, 5)
    assert result.last_usage == Usage(8, 3)
    assert len(llm.calls) == 2
    first_messages, definitions = llm.calls[0]
    assert first_messages[0] == Message("system", [TextPart("你是助手")])
    assert first_messages[-1].content == [
        TextPart("处理图片"),
        ImagePart("https://example.com/image.png"),
    ]
    assert [definition.name for definition in definitions] == ["echo"]

    second_messages, _ = llm.calls[1]
    tool_message = second_messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call-1"
    assert json.loads(tool_message.content[0].text) == {
        "success": True,
        "output": {"echo": "hello"},
        "error": None,
    }


def test_react_agent将tool返回图片保留在对应tool消息():
    image = ImagePart("assets/screenshot.png", "asset", "image/png")

    class ImageTool(EchoTool):
        def invoke(self, arguments):
            return ToolExecutionResult(True, {"captured": True}, images=(image,))

    registry = ToolRegistry()
    registry.register(ImageTool())
    llm = QueueLLM([tool_response(), final_response()])
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    agent.invoke("", [], "capture")

    tool_message = llm.calls[1][0][-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content[1] == image
    assert json.loads(tool_message.content[0].text)["images"] == [
        {
            "source": "assets/screenshot.png",
            "source_type": "asset",
            "media_type": "image/png",
        }
    ]


def test_react_agent向tool透传当前task身份和消息快照():
    received = {}

    class ContextAwareTool(EchoTool):
        def invoke(
            self,
            arguments,
            *,
            task_id=None,
            run_id=None,
            step=None,
            task_messages=(),
        ):
            received.update(
                task_id=task_id,
                run_id=run_id,
                step=step,
                task_messages=task_messages,
            )
            return super().invoke(arguments)

    registry = ToolRegistry()
    registry.register(ContextAwareTool())
    channel = active_channel()
    agent = ReActAgent(
        "thinking",
        QueueLLM([tool_response(), final_response()]),
        ToolExecutor(registry),
    )

    agent.invoke(
        "system",
        [Message("user", [TextPart("session history")])],
        "current task",
        tools=["echo"],
        run_control=channel,
    )

    assert received["task_id"] == "task-1"
    assert received["run_id"] == "run-1"
    assert received["step"] == 1
    task_messages = received["task_messages"]
    assert isinstance(task_messages, tuple)
    assert [message.role for message in task_messages] == [
        "user",
        "assistant",
    ]
    assert task_messages[0].content == [TextPart("current task")]


def test_react_agent_task_messages只包含当前task完整轨迹():
    agent, _ = make_agent([tool_response(), final_response()])

    result = agent.invoke(
        "system",
        [
            Message("user", [TextPart("old")]),
            Message("assistant", [TextPart("old answer")]),
        ],
        "current",
        tools=["echo"],
    )

    assert [message.role for message in result.task_messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.task_messages[0].content == [TextPart("current")]
    assert result.task_messages[1].tool_calls[0].id == "call-1"
    assert result.task_messages[2].tool_call_id == "call-1"
    assert result.task_messages[-1].content == [TextPart("done")]


def test_react_agent_tools空列表禁用工具():
    agent, llm = make_agent([final_response()])

    agent.invoke("", [], "hello", tools=[])

    assert llm.calls[0][1] == []


def test_react_agent_tools不传使用全部工具():
    agent, llm = make_agent([final_response()])

    agent.invoke("", [], "hello")

    assert [definition.name for definition in llm.calls[0][1]] == ["echo"]


def test_react_agent_同一实例连续调用不会泄漏消息():
    agent, llm = make_agent([final_response("first"), final_response("second")])

    first = agent.invoke("", [], "one")
    second = agent.invoke("", [], "two")

    assert first.message.content == [TextPart("first")]
    assert second.message.content == [TextPart("second")]
    assert llm.calls[0][0] == [Message("user", [TextPart("one")])]
    assert llm.calls[1][0] == [Message("user", [TextPart("two")])]


def test_react_agent_异步路径与同步语义一致():
    agent, llm = make_agent([tool_response(), final_response()])

    result = asyncio.run(
        agent.ainvoke(
            system_prompt="你是助手",
            history_messages=[],
            input_prompt="hello",
            tools=["echo"],
        )
    )

    assert result.message.content == [TextPart("done")]
    assert result.steps == 2
    assert len(llm.calls) == 2


def test_react_agent_异步调用在首个step前注入context():
    agent, llm = make_agent([final_response()])
    channel = active_channel()
    channel.add_context("async extra", source_id="external")

    result = asyncio.run(
        agent.ainvoke("", [], "original", run_control=channel)
    )

    assert llm.calls[0][0][-1].content[0].text == (
        "<runtime_context>\n1. async extra\n</runtime_context>\n\noriginal"
    )
    assert result.task_messages[0].content[0].text == (
        "<runtime_context>\n1. async extra\n</runtime_context>\n\noriginal"
    )
    assert [message.role for message in result.task_messages] == [
        "user", "assistant"
    ]


def active_channel() -> TaskChannel:
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    return channel


def test_react_agent首次step前按fifo合并运行中context():
    agent, llm = make_agent([final_response()])
    channel = active_channel()
    channel.add_context("first", source_id="memory")
    channel.add_context("second", source_id="external")

    result = agent.invoke("", [], "original", run_control=channel)

    assert llm.calls[0][0] == [
        Message(
            "user",
            [
                TextPart(
                    "<runtime_context>\n"
                    "1. first\n"
                    "2. second\n"
                    "</runtime_context>\n\n"
                    "original"
                )
            ],
        ),
    ]
    assert result.task_messages[0].content[0].text == (
        "<runtime_context>\n1. first\n2. second\n</runtime_context>\n\noriginal"
    )


def test_react_agent首次step把steer合并到当前用户消息末尾():
    agent, llm = make_agent([final_response()])
    channel = active_channel()
    channel.add_context("remember", source_id="memory")
    channel.add_steer("only tests")

    result = agent.invoke("", [], "original", run_control=channel)

    assert len(llm.calls[0][0]) == 1
    assert llm.calls[0][0][0].content[0].text == (
        "<runtime_context>\n1. remember\n</runtime_context>\n\n"
        "original\n\n"
        "<user_correction>\n1. only tests\n</user_correction>"
    )
    assert result.task_messages[0] == llm.calls[0][0][0]


def test_react_agent首次step保留原始图片并追加steer图片():
    agent, llm = make_agent([final_response()])
    channel = active_channel()
    original = ImagePart("assets/original.png", "asset", "image/png")
    correction = ImagePart("assets/correction.png", "asset", "image/png")
    channel.add_steer("compare", input_images=(correction,))

    result = agent.invoke(
        "", [], "original", input_images=[original], run_control=channel
    )

    assert llm.calls[0][0][0].content == [
        TextPart(
            "original\n\n<user_correction>\n1. compare\n</user_correction>"
        ),
        original,
        correction,
    ]
    assert result.task_messages[0] == llm.calls[0][0][0]


def test_react_agent工具完成后在下一step前注入context():
    channel = active_channel()

    class ContextTool(EchoTool):
        def invoke(self, arguments):
            result = super().invoke(arguments)
            channel.add_context("after tool", source_id="supervisor")
            return result

    registry = ToolRegistry()
    registry.register(ContextTool())
    llm = QueueLLM([tool_response(), final_response()])
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    agent.invoke("", [], "original", tools=["echo"], run_control=channel)

    second_messages = llm.calls[1][0]
    assert [message.role for message in second_messages] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert second_messages[-1].content[0].text == (
        "<runtime_context>\n1. after tool\n</runtime_context>"
    )


def test_react_agent工具完成后在下一step前注入steer并写入完整历史():
    channel = active_channel()

    class SteeringTool(EchoTool):
        def invoke(self, arguments):
            result = super().invoke(arguments)
            channel.add_steer("only change tests")
            return result

    registry = ToolRegistry()
    registry.register(SteeringTool())
    llm = QueueLLM([tool_response(), final_response()])
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    result = agent.invoke("", [], "original", tools=["echo"], run_control=channel)

    assert [message.role for message in llm.calls[1][0]] == [
        "user", "assistant", "tool", "user"
    ]
    assert llm.calls[1][0][-1].content[0].text == (
        "<user_correction>\n1. only change tests\n</user_correction>"
    )
    assert result.task_messages == tuple(llm.calls[1][0]) + (
        Message("assistant", [TextPart("done")]),
    )


def test_react_agent工具完成后steer图片进入下一条用户消息():
    channel = active_channel()
    image = ImagePart("assets/correction.png", "asset", "image/png")

    class SteeringTool(EchoTool):
        def invoke(self, arguments):
            result = super().invoke(arguments)
            channel.add_steer("look", input_images=(image,))
            return result

    registry = ToolRegistry()
    registry.register(SteeringTool())
    llm = QueueLLM([tool_response(), final_response()])
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    agent.invoke("", [], "original", tools=["echo"], run_control=channel)

    assert llm.calls[1][0][-1].content == [
        TextPart("<user_correction>\n1. look\n</user_correction>"),
        image,
    ]


def test_react_agent完成竞争时补充context触发额外step():
    channel = active_channel()

    class FinalBoundaryLLM(QueueLLM):
        def invoke(self, messages, tools=None):
            response = super().invoke(messages, tools)
            if len(self.calls) == 1:
                channel.add_context("late but accepted", source_id="memory")
            return response

    llm = FinalBoundaryLLM([final_response("draft"), final_response("revised")])
    agent = ReActAgent("thinking", llm, ToolExecutor(ToolRegistry()))

    result = agent.invoke("", [], "original", run_control=channel)

    assert result.message.content == [TextPart("revised")]
    assert result.steps == 2
    assert llm.calls[1][0][-1].content[0].text == (
        "<runtime_context>\n1. late but accepted\n</runtime_context>"
    )


def test_react_agent完成竞争后的新steer合并到尚未发送的user():
    channel = active_channel()

    class SameBoundarySteerChannel(TaskChannel):
        def close_or_drain(self, *, applied_before_step):
            batch = super().close_or_drain(
                applied_before_step=applied_before_step
            )
            if batch is not None:
                self.add_steer("second")
            return batch

    channel = SameBoundarySteerChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")

    class FirstSteerLLM(QueueLLM):
        def invoke(self, messages, tools=None):
            response = super().invoke(messages, tools)
            if len(self.calls) == 1:
                channel.add_steer("first")
            return response

    llm = FirstSteerLLM([final_response("draft"), final_response("done")])
    agent = ReActAgent("thinking", llm, ToolExecutor(ToolRegistry()))

    result = agent.invoke("", [], "original", run_control=channel)

    assert result.steps == 2
    assert [message.role for message in llm.calls[1][0]] == [
        "user", "assistant", "user"
    ]
    assert "1. first" in llm.calls[1][0][-1].content[0].text
    assert "1. second" in llm.calls[1][0][-1].content[0].text


def test_react_agent在下一step前执行harness上限检查():
    channel = TaskChannel("task-1", max_steps=1)
    channel.mark_preparing_context()
    channel.start_run("run-1")
    agent, llm = make_agent([tool_response()])

    with pytest.raises(MaxStepsExceededError) as caught:
        agent.invoke(
            "", [], "original", tools=["echo"], run_control=channel
        )

    assert caught.value.max_steps == 1
    assert caught.value.attempted_step == 2
    assert len(llm.calls) == 1
    assert channel.current_step == 1
    assert channel.history_checkpoint_usage == Usage(10, 2)
    assert [message.role for message in channel.history_checkpoint] == [
        "user",
        "assistant",
        "tool",
    ]


def test_react_agent取消后不启动tool():
    channel = active_channel()

    class CancellingLLM(QueueLLM):
        def invoke(self, messages, tools=None):
            response = super().invoke(messages, tools)
            channel.request_cancel("stop")
            return response

    class RecordingTool(EchoTool):
        def __init__(self):
            self.calls = 0

        def invoke(self, arguments):
            self.calls += 1
            return super().invoke(arguments)

    tool = RecordingTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = ReActAgent(
        "thinking",
        CancellingLLM([tool_response()]),
        ToolExecutor(registry),
    )

    with pytest.raises(AgentRunCancelled):
        agent.invoke("", [], "original", tools=["echo"], run_control=channel)

    assert tool.calls == 0


@pytest.mark.parametrize("async_mode", [False, True])
def test_react_agent在每个tool_batch前检查取消(async_mode):
    channel = active_channel()

    class CancellingTool(EchoTool):
        def __init__(self):
            self.calls = []

        def invoke(self, arguments):
            self.calls.append(arguments["value"])
            channel.request_cancel("stop after first batch")
            return super().invoke(arguments)

    tool = CancellingTool()
    registry = ToolRegistry()
    registry.register(tool)
    response = LLMResponse(
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall("call-1", "echo", {"value": "first"}),
                ToolCall("call-2", "echo", {"value": "second"}),
            ],
        ),
        finish_reason="tool_call",
    )
    agent = ReActAgent(
        "thinking",
        QueueLLM([response]),
        ToolExecutor(registry),
    )

    if async_mode:
        with pytest.raises(AgentRunCancelled):
            asyncio.run(
                agent.ainvoke(
                    "",
                    [],
                    "original",
                    tools=["echo"],
                    run_control=channel,
                )
            )
    else:
        with pytest.raises(AgentRunCancelled):
            agent.invoke(
                "",
                [],
                "original",
                tools=["echo"],
                run_control=channel,
            )

    assert tool.calls == ["first"]


def invalid_arguments_error():
    return InvalidToolCallArgumentsError(
        tool_name="echo",
        raw_arguments='{"value": "hello"',
        finish_reason="tool_call",
        detail="Expecting ',' delimiter",
    )


def test_invoke_在非法工具参数后重试同一step():
    agent, llm = make_agent([invalid_arguments_error(), final_response()])

    result = agent.invoke(
        system_prompt="你是助手",
        history_messages=[],
        input_prompt="执行任务",
        tools=["echo"],
    )

    assert result.message.content == [TextPart("done")]
    assert result.steps == 1
    assert len(llm.calls) == 2
    assert llm.calls[0][0] == llm.calls[1][0]


def test_ainvoke_在非法工具参数后重试同一step():
    agent, llm = make_agent([invalid_arguments_error(), final_response()])

    result = asyncio.run(
        agent.ainvoke(
            system_prompt="你是助手",
            history_messages=[],
            input_prompt="执行任务",
            tools=["echo"],
        )
    )

    assert result.message.content == [TextPart("done")]
    assert result.steps == 1
    assert len(llm.calls) == 2


def test_invoke_非法工具参数超过重试上限后失败且不增加step():
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    agent, llm = make_agent(
        [invalid_arguments_error() for _ in range(3)]
    )

    with pytest.raises(InvalidToolCallArgumentsError):
        agent.invoke(
            system_prompt="你是助手",
            history_messages=[],
            input_prompt="执行任务",
            tools=["echo"],
            run_control=channel,
        )

    assert len(llm.calls) == 3
    assert channel.current_step == 1
