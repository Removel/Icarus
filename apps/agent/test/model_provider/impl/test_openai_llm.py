import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.agent.src.model_config import ThinkMode
from apps.agent.src.model_provider.impl.openai_llm import OpenAILLM
from apps.agent.src.model_provider.types import (
    ImageAssetUnavailableError,
    ImagePart,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
)


class AsyncChunks:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self._chunks:
            yield chunk


def make_openai_llm():
    llm = object.__new__(OpenAILLM)
    llm.model_name = "test-model"
    llm.max_tokens = 128
    llm.temperature = 0.2
    llm.reasoning_effort = ThinkMode.HIGH
    llm._image_resolver = None
    llm._client = MagicMock()
    llm._async_client = MagicMock()
    return llm


def tool_delta(index, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def stream_chunk(
    content=None,
    tool_calls=None,
    finish_reason=None,
    usage=None,
):
    delta = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=None,
        reasoning=None,
    )
    choices = (
        [SimpleNamespace(delta=delta, finish_reason=finish_reason)]
        if content is not None or tool_calls is not None or finish_reason is not None
        else []
    )
    return SimpleNamespace(choices=choices, usage=usage)


def test_invoke_转换图片工具和完整响应():
    llm = make_openai_llm()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    reasoning_content=None,
                    reasoning=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="get_weather",
                                arguments='{"city":"Beijing"}',
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
    )
    llm._client.chat.completions.create.return_value = response
    messages = [
        Message(
            role="user",
            content=[
                TextPart("描述图片"),
                ImagePart("https://example.com/cat.png"),
            ],
        )
    ]
    tools = [
        ToolDefinition(
            name="get_weather",
            description="查询天气",
            input_schema={"type": "object"},
        )
    ]

    result = llm.invoke(messages, tools)

    request = llm._client.chat.completions.create.call_args.kwargs
    assert request["model"] == "test-model"
    assert request["messages"][0]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/cat.png"},
    }
    assert request["tools"][0]["function"]["name"] == "get_weather"
    assert request["reasoning_effort"] == "high"
    assert result.message.tool_calls == [
        ToolCall("call_1", "get_weather", {"city": "Beijing"})
    ]
    assert result.finish_reason == "tool_call"
    assert result.usage.total_tokens == 14


def test_asset图片转换为data_url(tmp_path):
    path = tmp_path / "asset.png"
    path.write_bytes(b"png-data")
    llm = make_openai_llm()
    llm._image_resolver = lambda image: path

    converted = llm._convert_message(
        Message("user", [ImagePart("assets/a.png", "asset", "image/png")])
    )

    assert converted["content"][0]["image_url"]["url"] == (
        "data:image/png;base64,cG5nLWRhdGE="
    )


def test_asset图片缺少resolver时明确失败():
    llm = make_openai_llm()
    with pytest.raises(ImageAssetUnavailableError, match="resolver"):
        llm._convert_message(
            Message("user", [ImagePart("assets/a.png", "asset")])
        )


def test_image_part兼容旧url关键字和二位置参数():
    keyword = ImagePart(url="https://example.com/a.png")
    positional = ImagePart("https://example.com/a.png", "image/png")

    assert keyword.source == "https://example.com/a.png"
    assert keyword.source_type == "url"
    assert positional.source_type == "url"
    assert positional.media_type == "image/png"


def test_invoke_携带助手工具调用和工具结果历史():
    llm = make_openai_llm()
    llm._client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="晴天",
                    reasoning_content=None,
                    reasoning=None,
                    tool_calls=None,
                ),
            )
        ],
        usage=None,
    )
    messages = [
        Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall("call_1", "get_weather", {"city": "Beijing"})
            ],
        ),
        Message(
            role="tool",
            content=[TextPart('{"weather":"sunny"}')],
            tool_call_id="call_1",
        ),
    ]

    result = llm.invoke(messages)

    request_messages = llm._client.chat.completions.create.call_args.kwargs["messages"]
    assert request_messages[0]["tool_calls"][0]["function"]["arguments"] == (
        '{"city": "Beijing"}'
    )
    assert request_messages[1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"weather":"sunny"}',
    }
    assert result.message.content == [TextPart("晴天")]


def test_stream_仅向上层返回完整工具调用():
    llm = make_openai_llm()
    llm._client.chat.completions.create.return_value = iter(
        [
            stream_chunk(
                tool_calls=[
                    tool_delta(
                        0,
                        call_id="call_1",
                        name="get_weather",
                        arguments='{"city"',
                    )
                ]
            ),
            stream_chunk(
                tool_calls=[tool_delta(0, arguments=':"Beijing"}')]
            ),
            stream_chunk(finish_reason="tool_calls"),
            stream_chunk(
                usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3)
            ),
        ]
    )

    chunks = list(llm.stream([Message("user", [TextPart("天气")])]))

    assert [chunk for chunk in chunks if chunk.tool_calls] == [
        chunks[0]
    ]
    assert chunks[0].tool_calls == [
        ToolCall("call_1", "get_weather", {"city": "Beijing"})
    ]
    assert chunks[0].finish_reason == "tool_call"
    assert chunks[1].usage.total_tokens == 11


def test_ainvoke和astream_使用异步客户端():
    llm = make_openai_llm()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="hello",
                    reasoning_content=None,
                    reasoning=None,
                    tool_calls=None,
                ),
            )
        ],
        usage=None,
    )
    llm._async_client.chat.completions.create = AsyncMock(
        side_effect=[
            response,
            AsyncChunks(
                [
                    stream_chunk(content="he"),
                    stream_chunk(content="llo", finish_reason="stop"),
                ]
            ),
        ]
    )

    async def run():
        response_result = await llm.ainvoke(
            [Message("user", [TextPart("hi")])]
        )
        stream_result = [
            chunk
            async for chunk in llm.astream(
                [Message("user", [TextPart("hi")])]
            )
        ]
        return response_result, stream_result

    response_result, stream_result = asyncio.run(run())

    assert response_result.message.content == [TextPart("hello")]
    assert "".join(chunk.text_delta for chunk in stream_result) == "hello"
    assert stream_result[-1].finish_reason == "stop"


def test_close和aclose_关闭对应客户端():
    llm = make_openai_llm()
    llm._async_client.close = AsyncMock()

    llm.close()
    asyncio.run(llm.aclose())

    assert llm._client.close.call_count == 2
    llm._async_client.close.assert_awaited_once()
