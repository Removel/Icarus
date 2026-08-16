import asyncio
import json

from apps.agent.src.agent_orchestration.capability import ReActAgent
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
