import asyncio

from apps.agent.src.agent_orchestration.capability import AgentResponse, BaseAgent
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
from apps.agent.src.agent_orchestration.tools import (
    BaseTool,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
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
    ):
        return AgentResponse(Message("assistant", [TextPart("done")]))

    async def ainvoke(
        self,
        system_prompt,
        history_messages,
        input_prompt,
        input_images=None,
        tools=None,
    ):
        return self.invoke(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
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
