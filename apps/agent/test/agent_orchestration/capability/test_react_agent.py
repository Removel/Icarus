import asyncio
import json

import pytest

from apps.agent.src.agent_orchestration.capability import ReActAgent
from apps.agent.src.agent_orchestration.run_control import (
    AgentRunCancelled,
    MaxStepsExceededError,
    TaskChannel,
)
from apps.agent.src.agent_orchestration.tools import (
    BaseTool,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
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
        return self.responses.pop(0)

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
        "<runtime_context>\n1. async extra\n</runtime_context>"
    )
    assert result.task_messages[1].content[0].text == (
        "<runtime_context>\n1. async extra\n</runtime_context>"
    )


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
        Message("user", [TextPart("original")]),
        Message(
            "user",
            [
                TextPart(
                    "<runtime_context>\n"
                    "1. first\n"
                    "2. second\n"
                    "</runtime_context>"
                )
            ],
        ),
    ]
    assert result.task_messages[1].content[0].text == (
        "<runtime_context>\n1. first\n2. second\n</runtime_context>"
    )


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
