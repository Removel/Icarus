import asyncio
import json

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
    ReActAgent,
)
from apps.agent.src.agent_orchestration.tools import (
    BaseTool,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMStreamChunk,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
    Usage,
)


class StreamQueueLLM(BaseLLM):
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def invoke(self, messages, tools=None):
        raise NotImplementedError

    async def ainvoke(self, messages, tools=None):
        raise NotImplementedError

    def stream(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        yield from self.streams.pop(0)

    async def astream(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        for chunk in self.streams.pop(0):
            await asyncio.sleep(0)
            yield chunk

    def close(self):
        pass

    async def aclose(self):
        pass


class EchoTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition("echo", "返回输入", {"type": "object"})

    def invoke(self, arguments):
        return ToolExecutionResult(True, {"echo": arguments["value"]})


def make_agent(streams):
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = StreamQueueLLM(streams)
    return ReActAgent("thinking", llm, ToolExecutor(registry)), llm


def tool_stream():
    return [
        LLMStreamChunk(text_delta="让我"),
        LLMStreamChunk(text_delta="调用工具"),
        LLMStreamChunk(reasoning_delta="内部推理"),
        LLMStreamChunk(
            tool_calls=[ToolCall("call-1", "echo", {"value": "hello"})],
            usage=Usage(10, 4),
            finish_reason="tool_call",
        ),
    ]


def final_stream():
    return [
        LLMStreamChunk(text_delta="最终"),
        LLMStreamChunk(
            text_delta="回答",
            reasoning_delta="完成推理",
            usage=Usage(8, 3),
            finish_reason="stop",
        ),
    ]


def test_stream_完成多轮工具调用且不流出reasoning():
    agent, llm = make_agent([tool_stream(), final_stream()])

    events = list(agent.stream("你是助手", [], "执行任务", tools=["echo"]))

    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentTextDeltaEvent,
        AgentToolStartedEvent,
        AgentToolCompletedEvent,
        AgentTextDeltaEvent,
        AgentTextDeltaEvent,
        AgentCompletedEvent,
    ]
    assert [event.text for event in events if isinstance(event, AgentTextDeltaEvent)] == [
        "让我",
        "调用工具",
        "最终",
        "回答",
    ]
    assert {event.task_id for event in events} == {None}
    started = next(event for event in events if isinstance(event, AgentToolStartedEvent))
    completed = next(
        event for event in events if isinstance(event, AgentToolCompletedEvent)
    )
    assert started.tool_call.arguments == {"value": "hello"}
    assert completed.result.output == {"echo": "hello"}

    final = events[-1]
    assert isinstance(final, AgentCompletedEvent)
    assert final.response.message.content == [TextPart("最终回答")]
    assert final.response.reasoning == "内部推理\n完成推理"
    assert final.response.usage == Usage(18, 7)
    assert final.response.steps == 2
    assert [message.role for message in final.response.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    tool_message = llm.calls[1][0][-1]
    assert json.loads(tool_message.content[0].text)["success"] is True


def test_astream_与同步流保持相同事件语义():
    agent, _ = make_agent([tool_stream(), final_stream()])

    async def run():
        return [
            event
            async for event in agent.astream(
                "你是助手",
                [],
                "执行任务",
                tools=["echo"],
            )
        ]

    events = asyncio.run(run())

    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentTextDeltaEvent,
        AgentToolStartedEvent,
        AgentToolCompletedEvent,
        AgentTextDeltaEvent,
        AgentTextDeltaEvent,
        AgentCompletedEvent,
    ]
    assert events[-1].response.finish_reason == "stop"


def test_stream_先流出error再抛出原始异常():
    class FailingLLM(StreamQueueLLM):
        def stream(self, messages, tools=None):
            yield LLMStreamChunk(text_delta="开始")
            raise RuntimeError("stream failed")

    registry = ToolRegistry()
    agent = ReActAgent("thinking", FailingLLM([]), ToolExecutor(registry))
    generator = agent.stream("", [], "hello", tools=[])

    first = next(generator)
    second = next(generator)

    assert isinstance(first, AgentTextDeltaEvent)
    assert isinstance(second, AgentErrorEvent)
    assert second.error_message == "stream failed"
    with pytest.raises(RuntimeError, match="stream failed"):
        next(generator)
