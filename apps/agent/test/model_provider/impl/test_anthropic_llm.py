import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from apps.agent.src.model_provider.impl.anthropic_llm import AnthropicLLM
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
)


class AsyncEvents:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


def make_anthropic_llm():
    llm = object.__new__(AnthropicLLM)
    llm.model_name = "test-model"
    llm.max_tokens = 128
    llm.temperature = 0.2
    llm.thinking_budget = 1024
    llm._image_resolver = None
    llm._client = MagicMock()
    llm._async_client = MagicMock()
    return llm


def anthropic_response():
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="分析"),
            SimpleNamespace(type="text", text="需要查询"),
            SimpleNamespace(
                type="tool_use",
                id="tool_1",
                name="get_weather",
                input={"city": "Beijing"},
            ),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        stop_reason="tool_use",
    )


def anthropic_events():
    return [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=10)
            ),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use",
                id="tool_1",
                name="get_weather",
                input={},
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(
                type="input_json_delta",
                partial_json='{"city"',
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(
                type="input_json_delta",
                partial_json=':"Beijing"}',
            ),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(output_tokens=5),
            delta=SimpleNamespace(stop_reason="tool_use"),
        ),
        SimpleNamespace(type="message_stop"),
    ]


def test_invoke_转换系统图片工具和完整响应():
    llm = make_anthropic_llm()
    llm._client.messages.create.return_value = anthropic_response()
    messages = [
        Message("system", [TextPart("你是助手")]),
        Message(
            "user",
            [
                TextPart("描述图片"),
                ImagePart("https://example.com/cat.png"),
            ],
        ),
    ]
    tools = [
        ToolDefinition(
            "get_weather",
            "查询天气",
            {"type": "object"},
        )
    ]

    result = llm.invoke(messages, tools)

    request = llm._client.messages.create.call_args.kwargs
    assert request["system"] == "你是助手"
    assert request["messages"][0]["content"][1] == {
        "type": "image",
        "source": {
            "type": "url",
            "url": "https://example.com/cat.png",
        },
    }
    assert request["tools"][0]["input_schema"] == {"type": "object"}
    assert request["thinking"] == {
        "type": "enabled",
        "budget_tokens": 1024,
    }
    assert result.reasoning == "分析"
    assert result.message.content == [TextPart("需要查询")]
    assert result.message.tool_calls == [
        ToolCall("tool_1", "get_weather", {"city": "Beijing"})
    ]
    assert result.finish_reason == "tool_call"


def test_asset图片转换为base64_source(tmp_path):
    path = tmp_path / "asset.png"
    path.write_bytes(b"png-data")
    llm = make_anthropic_llm()
    llm._image_resolver = lambda image: path

    converted = llm._convert_message(
        Message("user", [ImagePart("assets/a.png", "asset", "image/png")])
    )

    assert converted["content"][0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": "cG5nLWRhdGE=",
        },
    }


def test_invoke_携带助手工具调用和工具结果历史():
    llm = make_anthropic_llm()
    llm._client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="晴天")],
        usage=SimpleNamespace(input_tokens=12, output_tokens=2),
        stop_reason="end_turn",
    )
    messages = [
        Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall("tool_1", "get_weather", {"city": "Beijing"})
            ],
        ),
        Message(
            role="tool",
            content=[TextPart('{"weather":"sunny"}')],
            tool_call_id="tool_1",
        ),
    ]

    result = llm.invoke(messages)

    request_messages = llm._client.messages.create.call_args.kwargs["messages"]
    assert request_messages[0]["content"][0] == {
        "type": "tool_use",
        "id": "tool_1",
        "name": "get_weather",
        "input": {"city": "Beijing"},
    }
    assert request_messages[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tool_1",
        "content": '{"weather":"sunny"}',
    }
    assert result.message.content == [TextPart("晴天")]


def test_stream_仅向上层返回完整工具调用():
    llm = make_anthropic_llm()
    llm._client.messages.create.return_value = iter(anthropic_events())

    chunks = list(llm.stream([Message("user", [TextPart("天气")])]))

    assert chunks[0].tool_calls == [
        ToolCall("tool_1", "get_weather", {"city": "Beijing"})
    ]
    assert chunks[1].usage.total_tokens == 15
    assert chunks[1].finish_reason == "tool_call"


def test_ainvoke和astream_使用异步客户端():
    llm = make_anthropic_llm()
    llm._async_client.messages.create = AsyncMock(
        side_effect=[
            anthropic_response(),
            AsyncEvents(anthropic_events()),
        ]
    )

    async def run():
        response_result = await llm.ainvoke(
            [Message("user", [TextPart("天气")])]
        )
        stream_result = [
            chunk
            async for chunk in llm.astream(
                [Message("user", [TextPart("天气")])]
            )
        ]
        return response_result, stream_result

    response_result, stream_result = asyncio.run(run())

    assert response_result.message.tool_calls[0].name == "get_weather"
    assert stream_result[0].tool_calls[0].arguments == {"city": "Beijing"}
    assert stream_result[-1].finish_reason == "tool_call"


def test_close和aclose_关闭对应客户端():
    llm = make_anthropic_llm()
    llm._async_client.close = AsyncMock()

    llm.close()
    asyncio.run(llm.aclose())

    assert llm._client.close.call_count == 2
    llm._async_client.close.assert_awaited_once()
