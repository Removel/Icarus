import asyncio

from apps.agent.src.agent_orchestration.capability import AgentResponse, BaseAgent
from apps.agent.src.agent_orchestration.capability.types import (
    AgentCompletedEvent,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookDispatcher,
    HookEvent,
    HookRegistry,
)
from apps.agent.src.agent_orchestration.hooks.wrappers import (
    ObservableAgent,
    ObservableLLM,
    ObservableToolExecutor,
)
from apps.agent.src.agent_orchestration.run_control import TaskChannel
from apps.agent.src.agent_orchestration.tools import (
    BaseTool,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
    LLMStreamChunk,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
)


class RecordingHook(BaseHook):
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    def handle(self, event: HookEvent) -> None:
        self.events.append(event)


class StubLLM(BaseLLM):
    def invoke(self, messages, tools=None):
        return LLMResponse(Message("assistant", [TextPart("done")]), finish_reason="stop")

    async def ainvoke(self, messages, tools=None):
        return self.invoke(messages, tools)

    def stream(self, messages, tools=None):
        return iter(
            [
                LLMStreamChunk(text_delta="he"),
                LLMStreamChunk(reasoning_delta="think"),
                LLMStreamChunk(
                    text_delta="llo",
                    finish_reason="stop",
                ),
            ]
        )

    async def astream(self, messages, tools=None):
        for chunk in self.stream(messages, tools):
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
        return ToolExecutionResult(True, arguments)


class StubAgent(BaseAgent):
    @property
    def model_role(self):
        return "thinking"

    def invoke(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
        run_control=None,
    ):
        return AgentResponse(Message("assistant", [TextPart("done")]))

    async def ainvoke(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
        run_control=None,
    ):
        return self.invoke(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
        )

    def stream(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
        run_control=None,
    ):
        yield AgentTextDeltaEvent(step=1, text="done")
        yield AgentCompletedEvent(
            step=1,
            response=self.invoke(
                system_prompt,
                history_messages,
                input_prompt,
                input_images,
                tools,
            ),
        )

    async def astream(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
        run_control=None,
    ):
        yield AgentTextDeltaEvent(step=1, text="done")
        yield AgentCompletedEvent(
            step=1,
            response=await self.ainvoke(
                system_prompt,
                history_messages,
                input_prompt,
                input_images,
                tools,
            ),
        )


def make_observed_components():
    registry = HookRegistry()
    recorder = RecordingHook()
    registry.register("*", recorder)
    dispatcher = HookDispatcher(registry)
    llm = ObservableLLM(StubLLM(), dispatcher)
    tool_registry = ToolRegistry()
    tool_registry.register(EchoTool())
    executor = ObservableToolExecutor(ToolExecutor(tool_registry), dispatcher)
    agent = ObservableAgent(StubAgent(), dispatcher)
    return recorder, llm, executor, agent


def test_observable_agent_llm_tool_共享同一个run_id():
    recorder, llm, executor, agent = make_observed_components()

    class CompositeAgent(StubAgent):
        def invoke(
            self,
            system_prompt,
            history_messages,
            input_prompt,
            input_images=None,
            tools=None,
            run_control=None,
        ):
            llm.invoke([Message("user", [TextPart(input_prompt)])])
            executor.execute(ToolCall("call-1", "echo", {"value": 1}))
            return super().invoke(
                system_prompt,
                history_messages,
                input_prompt,
                input_images,
                tools,
            )

    observed = ObservableAgent(CompositeAgent(), agent._dispatcher)
    result = observed.invoke("system", [], "hello")

    assert result.message.content == [TextPart("done")]
    assert [event.name for event in recorder.events] == [
        "agent.invoke",
        "llm.invoke",
        "llm.invoke",
        "tool.execute",
        "tool.execute",
        "agent.invoke",
    ]
    assert [event.phase for event in recorder.events] == [
        "before",
        "before",
        "after",
        "before",
        "after",
        "after",
    ]
    assert len({event.run_id for event in recorder.events}) == 1
    assert recorder.events[0].run_id is not None
    llm_events = [event for event in recorder.events if event.name == "llm.invoke"]
    assert llm_events[0].data["llm_call_id"] == llm_events[1].data["llm_call_id"]
    tool_events = [event for event in recorder.events if event.name == "tool.execute"]
    assert (
        tool_events[0].data["tool_execution_id"]
        == tool_events[1].data["tool_execution_id"]
    )


def test_observable_components_异步接口保持透明():
    recorder, llm, executor, agent = make_observed_components()

    async def run():
        llm_response = await llm.ainvoke([Message("user", [TextPart("hello")])])
        tool_result = await executor.aexecute(
            ToolCall("call-1", "echo", {"value": 1})
        )
        agent_response = await agent.ainvoke("system", [], "hello")
        return llm_response, tool_result, agent_response

    llm_response, tool_result, agent_response = asyncio.run(run())

    assert llm_response.message.content == [TextPart("done")]
    assert tool_result.output == {"value": 1}
    assert agent_response.message.content == [TextPart("done")]
    assert len(recorder.events) == 6


def test_observable_tool_executor_同步并发保持hook_context():
    recorder, _, executor, agent = make_observed_components()

    class ConcurrentAgent(StubAgent):
        def invoke(
            self,
            system_prompt,
            history_messages,
            input_prompt,
            input_images=None,
            tools=None,
            run_control=None,
        ):
            executor.execute_many(
                [
                    ToolCall("call-1", "echo", {"value": 1}),
                    ToolCall("call-2", "echo", {"value": 2}),
                ]
            )
            return super().invoke(
                system_prompt,
                history_messages,
                input_prompt,
                input_images,
                tools,
            )

    ObservableAgent(ConcurrentAgent(), agent._dispatcher).invoke(
        "system",
        [],
        "hello",
    )

    tool_events = [event for event in recorder.events if event.name == "tool.execute"]
    assert len(tool_events) == 4
    assert len({event.run_id for event in tool_events}) == 1
    assert tool_events[0].run_id is not None


def test_observable_llm_stream_只记录聚合生命周期():
    recorder, llm, _, _ = make_observed_components()

    chunks = list(llm.stream([Message("user", [TextPart("hello")])]))

    assert "".join(chunk.text_delta for chunk in chunks) == "hello"
    stream_events = [event for event in recorder.events if event.name == "llm.stream"]
    assert [event.phase for event in stream_events] == ["before", "after"]
    assert (
        stream_events[0].data["llm_call_id"]
        == stream_events[1].data["llm_call_id"]
    )
    assert stream_events[1].data["stream_result"] == {
        "text": "hello",
        "reasoning": "think",
        "tool_calls": [],
        "usage": None,
        "finish_reason": "stop",
    }


def test_observable_agent_stream_保持事件并记录生命周期():
    recorder, _, _, agent = make_observed_components()

    events = list(agent.stream("system", [], "hello"))

    assert [type(event) for event in events] == [
        AgentTextDeltaEvent,
        AgentCompletedEvent,
    ]
    stream_events = [
        event for event in recorder.events if event.name == "agent.stream"
    ]
    assert [event.phase for event in stream_events] == ["before", "after"]
    assert len({event.run_id for event in stream_events}) == 1
    assert stream_events[0].run_id is not None


def test_observable_agent_async_stream可在不同context关闭():
    async def run():
        recorder, _, _, agent = make_observed_components()
        stream = agent.astream("system", [], "hello")

        async def read_one():
            return await anext(stream)

        first = await asyncio.create_task(read_one())
        await asyncio.create_task(stream.aclose())
        return first, recorder.events

    first, events = asyncio.run(run())

    assert isinstance(first, AgentTextDeltaEvent)
    stream_events = [event for event in events if event.name == "agent.stream"]
    assert [event.phase for event in stream_events] == ["before"]


def test_observable_agent使用业务run_id():
    recorder, _, _, agent = make_observed_components()
    channel = TaskChannel("task-1")
    channel.mark_preparing_context()
    channel.start_run("business-run")

    agent.invoke("system", [], "hello", run_control=channel)

    assert {event.run_id for event in recorder.events} == {"business-run"}


def test_observable_agent错误hook包含traceback但事件层不依赖异常():
    class FailingAgent(StubAgent):
        def invoke(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("broken")

    registry = HookRegistry()
    recorder = RecordingHook()
    registry.register("*", recorder)
    agent = ObservableAgent(FailingAgent(), HookDispatcher(registry))

    try:
        agent.invoke("", [], "hello")
    except RuntimeError:
        pass

    error = next(event for event in recorder.events if event.phase == "error")
    assert error.data["error_type"] == "RuntimeError"
    assert "RuntimeError: broken" in error.data["traceback"]
