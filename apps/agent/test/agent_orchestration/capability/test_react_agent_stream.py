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
from apps.agent.src.agent_orchestration.run_control import (
    AgentRunCancelled,
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


def active_channel() -> TaskChannel:
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("run-1")
    return channel


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
    assert [message.role for message in final.response.task_messages] == [
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


def test_stream和astream都在首个step前注入context():
    sync_agent, sync_llm = make_agent([final_stream()])
    sync_channel = active_channel()
    sync_channel.add_context("sync extra", source_id="external")

    sync_events = list(
        sync_agent.stream("", [], "original", run_control=sync_channel)
    )

    async def run_async():
        async_agent, async_llm = make_agent([final_stream()])
        async_channel = active_channel()
        async_channel.add_context("async extra", source_id="external")
        events = [
            event
            async for event in async_agent.astream(
                "",
                [],
                "original",
                run_control=async_channel,
            )
        ]
        return events, async_llm

    async_events, async_llm = asyncio.run(run_async())

    assert sync_llm.calls[0][0][-1].content[0].text == (
        "<runtime_context>\n1. sync extra\n</runtime_context>"
    )
    assert async_llm.calls[0][0][-1].content[0].text == (
        "<runtime_context>\n1. async extra\n</runtime_context>"
    )
    assert sync_events[-1].response.task_messages[1].content[0].text == (
        "<runtime_context>\n1. sync extra\n</runtime_context>"
    )
    assert async_events[-1].response.task_messages[1].content[0].text == (
        "<runtime_context>\n1. async extra\n</runtime_context>"
    )


def test_stream和astream取消后都不启动tool():
    sync_channel = active_channel()

    class CancellingLLM(StreamQueueLLM):
        def stream(self, messages, tools=None):
            yield from super().stream(messages, tools)
            sync_channel.request_cancel("stop")

    sync_agent = ReActAgent(
        "thinking",
        CancellingLLM([tool_stream()]),
        ToolExecutor(ToolRegistry()),
    )
    with pytest.raises(AgentRunCancelled):
        list(sync_agent.stream("", [], "original", run_control=sync_channel))

    async def run_async():
        async_channel = active_channel()

        class AsyncCancellingLLM(StreamQueueLLM):
            async def astream(self, messages, tools=None):
                async for chunk in super().astream(messages, tools):
                    yield chunk
                async_channel.request_cancel("stop")

        async_agent = ReActAgent(
            "thinking",
            AsyncCancellingLLM([tool_stream()]),
            ToolExecutor(ToolRegistry()),
        )
        with pytest.raises(AgentRunCancelled):
            async for _ in async_agent.astream(
                "",
                [],
                "original",
                run_control=async_channel,
            ):
                pass

    asyncio.run(run_async())


def test_astream取消时checkpoint保留完整tool_step并排除部分assistant():
    channel = active_channel()

    class CancelDuringSecondStepLLM(StreamQueueLLM):
        async def astream(self, messages, tools=None):
            self.calls.append((list(messages), list(tools or [])))
            stream = self.streams.pop(0)
            if len(self.calls) == 2:
                channel.request_cancel("stop during answer")
            for chunk in stream:
                yield chunk

    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = ReActAgent(
        "thinking",
        CancelDuringSecondStepLLM([tool_stream(), final_stream()]),
        ToolExecutor(registry),
    )

    async def run():
        with pytest.raises(AgentRunCancelled):
            async for _ in agent.astream(
                "system",
                [],
                "original",
                tools=["echo"],
                run_control=channel,
            ):
                pass

    asyncio.run(run())

    assert [message.role for message in channel.history_checkpoint] == [
        "user",
        "assistant",
        "tool",
    ]
    assert channel.history_checkpoint[1].tool_calls[0].id == "call-1"
    assert channel.history_checkpoint[2].tool_call_id == "call-1"


def test_astream首次llm中取消只保留已提交输入不保留部分assistant():
    async def run():
        channel = active_channel()

        class BlockingLLM(StreamQueueLLM):
            def __init__(self):
                super().__init__([])
                self.started = asyncio.Event()

            async def astream(self, messages, tools=None):
                self.calls.append((list(messages), list(tools or [])))
                yield LLMStreamChunk(text_delta="partial")
                self.started.set()
                await asyncio.Event().wait()

        llm = BlockingLLM()
        agent = ReActAgent("thinking", llm, ToolExecutor(ToolRegistry()))

        async def consume():
            async for _ in agent.astream(
                "system",
                [],
                "original",
                run_control=channel,
            ):
                pass

        task = asyncio.create_task(consume())
        await llm.started.wait()
        channel.request_cancel("stop during first answer")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return channel

    channel = asyncio.run(run())

    assert channel.history_checkpoint == (
        Message("user", [TextPart("original")]),
    )


def test_astream_tool执行中取消不保留未完成toolcall():
    async def run():
        channel = active_channel()
        started = asyncio.Event()

        class BlockingTool(EchoTool):
            @property
            def definition(self):
                return ToolDefinition("block", "等待取消", {"type": "object"})

            async def ainvoke(self, arguments):
                del arguments
                started.set()
                await asyncio.Event().wait()

        registry = ToolRegistry()
        registry.register(BlockingTool())
        response = [
            LLMStreamChunk(
                tool_calls=[ToolCall("call-1", "block", {})],
                finish_reason="tool_call",
            )
        ]
        agent = ReActAgent(
            "thinking",
            StreamQueueLLM([response]),
            ToolExecutor(registry),
        )

        async def consume():
            async for _ in agent.astream(
                "system",
                [],
                "original",
                tools=["block"],
                run_control=channel,
            ):
                pass

        task = asyncio.create_task(consume())
        await started.wait()
        channel.request_cancel("stop during tool")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return channel

    channel = asyncio.run(run())

    assert channel.history_checkpoint == (
        Message("user", [TextPart("original")]),
    )


def test_astream最后一个tool完成事件可见时完整step已进入checkpoint():
    async def run():
        channel = active_channel()
        agent, _ = make_agent([tool_stream(), final_stream()])
        events = []

        with pytest.raises(AgentRunCancelled):
            async for event in agent.astream(
                "system",
                [],
                "original",
                tools=["echo"],
                run_control=channel,
            ):
                events.append(event)
                if isinstance(event, AgentToolCompletedEvent):
                    channel.request_cancel("stop after tool")
        return events, channel

    events, channel = asyncio.run(run())

    assert any(isinstance(event, AgentToolCompletedEvent) for event in events)
    assert [message.role for message in channel.history_checkpoint] == [
        "user",
        "assistant",
        "tool",
    ]


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
