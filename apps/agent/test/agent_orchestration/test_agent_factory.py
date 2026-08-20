from apps.agent.src.agent_orchestration import AgentFactory
from apps.agent.src.agent_orchestration.hooks import BaseHook, HookEvent, HookRegistry
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult, ToolRegistry
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
)


class StubLLM(BaseLLM):
    def __init__(self, role):
        self.role = role
        self.calls = 0
        self.closed = False

    def invoke(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1 and tools:
            return LLMResponse(
                Message(
                    "assistant",
                    [],
                    tool_calls=[ToolCall("call-1", "echo", {"value": self.role})],
                ),
                finish_reason="tool_call",
            )
        return LLMResponse(
            Message("assistant", [TextPart(f"{self.role}-done")]),
            finish_reason="stop",
        )

    async def ainvoke(self, messages, tools=None):
        return self.invoke(messages, tools)

    def stream(self, messages, tools=None):
        return iter(())

    async def astream(self, messages, tools=None):
        if False:
            yield

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


class StubLLMFactory:
    def __init__(self):
        self.created = {}

    def create_llm(self, role):
        llm = StubLLM(role)
        self.created[role] = llm
        return llm


class EchoTool(BaseTool):
    @property
    def definition(self):
        return ToolDefinition("echo", "返回输入", {"type": "object"})

    def invoke(self, arguments):
        return ToolExecutionResult(True, arguments)


class RecordingHook(BaseHook):
    def __init__(self):
        self.events: list[HookEvent] = []

    def handle(self, event):
        self.events.append(event)


def make_factory():
    tool_registry = ToolRegistry()
    tool_registry.register(EchoTool())
    hook_registry = HookRegistry()
    recorder = RecordingHook()
    hook_registry.register("*", recorder)
    llm_factory = StubLLMFactory()
    factory = AgentFactory(
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        hook_registry=hook_registry,
        register_builtin_tools=False,
    )
    return factory, llm_factory, recorder


def test_agent_factory_按模型角色缓存无状态agent():
    factory, llm_factory, _ = make_factory()

    first = factory.get_agent("thinking")
    second = factory.get_agent("thinking")
    perception = factory.get_agent("perception")

    assert first is second
    assert perception is not first
    assert set(llm_factory.created) == {"thinking", "perception"}


def test_agent_factory_组装完整react和hook轨迹():
    factory, _, recorder = make_factory()
    agent = factory.get_agent("thinking")

    result = agent.invoke(
        system_prompt="你是助手",
        history_messages=[],
        input_prompt="执行工具",
        tools=["echo"],
    )

    assert result.message.content == [TextPart("thinking-done")]
    assert [event.name for event in recorder.events] == [
        "agent.invoke",
        "llm.invoke",
        "llm.invoke",
        "tool.execute",
        "tool.execute",
        "llm.invoke",
        "llm.invoke",
        "agent.invoke",
    ]
    assert len({event.run_id for event in recorder.events}) == 1


def test_agent_factory_close_释放已创建llm():
    factory, llm_factory, _ = make_factory()
    factory.get_agent("thinking")
    factory.get_agent("perception")

    factory.close()

    assert all(llm.closed for llm in llm_factory.created.values())
    assert factory._agents == {}
    assert factory._llms == {}


def test_agent_factory_close一个llm失败仍关闭其余并清空缓存():
    factory, llm_factory, _ = make_factory()
    factory.get_agent("thinking")
    factory.get_agent("perception")
    thinking = llm_factory.created["thinking"]
    perception = llm_factory.created["perception"]

    def fail_close():
        thinking.closed = True
        raise RuntimeError("thinking close failed")

    thinking.close = fail_close

    try:
        factory.close()
    except RuntimeError as error:
        assert str(error) == "thinking close failed"
    else:
        raise AssertionError("close error should be preserved")

    assert thinking.closed is True
    assert perception.closed is True
    assert factory._agents == {}
    assert factory._llms == {}


def test_agent_factory_aclose一个llm失败仍关闭其余并清空缓存():
    import asyncio

    async def run():
        factory, llm_factory, _ = make_factory()
        factory.get_agent("thinking")
        factory.get_agent("perception")
        thinking = llm_factory.created["thinking"]
        perception = llm_factory.created["perception"]

        async def fail_close():
            thinking.closed = True
            raise RuntimeError("thinking close failed")

        thinking.aclose = fail_close
        try:
            await factory.aclose()
        except RuntimeError as error:
            assert str(error) == "thinking close failed"
        else:
            raise AssertionError("close error should be preserved")
        return factory, thinking, perception

    factory, thinking, perception = asyncio.run(run())

    assert thinking.closed is True
    assert perception.closed is True
    assert factory._agents == {}
    assert factory._llms == {}
