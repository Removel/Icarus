import base64
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Callable

from openai import AsyncOpenAI, OpenAI

from apps.agent.src.model_config import ThinkMode
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    FinishReason,
    ImageAssetUnavailableError,
    ImagePart,
    LLMResponse,
    LLMStreamChunk,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
    Usage,
)


class OpenAILLM(BaseLLM):
    """使用 OpenAI Chat Completions 协议完成单次 LLM 调用。"""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: ThinkMode | None = None,
        image_resolver: Callable[[ImagePart], Path] | None = None,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self._image_resolver = image_resolver
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._async_client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def invoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        response = self._client.chat.completions.create(
            **self._request_params(messages, tools),
        )
        return self._convert_response(response)

    async def ainvoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        response = await self._async_client.chat.completions.create(
            **self._request_params(messages, tools),
        )
        return self._convert_response(response)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        stream = self._client.chat.completions.create(
            **self._request_params(messages, tools),
            stream=True,
            stream_options={"include_usage": True},
        )
        tool_calls: dict[int, dict[str, str]] = {}
        emitted_tool_calls = False

        for chunk in stream:
            usage = self._convert_usage(chunk.usage)
            if not chunk.choices:
                if usage is not None:
                    yield LLMStreamChunk(usage=usage)
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            self._collect_tool_call_deltas(delta.tool_calls, tool_calls)
            text_delta = delta.content or ""
            reasoning_delta = self._reasoning_text(delta)
            finish_reason = self._convert_finish_reason(choice.finish_reason)
            completed_calls: list[ToolCall] = []

            if finish_reason is not None and tool_calls:
                completed_calls = self._complete_tool_calls(tool_calls)
                emitted_tool_calls = True

            if (
                text_delta
                or reasoning_delta
                or completed_calls
                or usage is not None
                or finish_reason is not None
            ):
                yield LLMStreamChunk(
                    text_delta=text_delta,
                    reasoning_delta=reasoning_delta,
                    tool_calls=completed_calls,
                    usage=usage,
                    finish_reason=finish_reason,
                )

        if tool_calls and not emitted_tool_calls:
            yield LLMStreamChunk(
                tool_calls=self._complete_tool_calls(tool_calls),
                finish_reason="tool_call",
            )

    async def astream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        stream = await self._async_client.chat.completions.create(
            **self._request_params(messages, tools),
            stream=True,
            stream_options={"include_usage": True},
        )
        tool_calls: dict[int, dict[str, str]] = {}
        emitted_tool_calls = False

        async for chunk in stream:
            usage = self._convert_usage(chunk.usage)
            if not chunk.choices:
                if usage is not None:
                    yield LLMStreamChunk(usage=usage)
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            self._collect_tool_call_deltas(delta.tool_calls, tool_calls)
            text_delta = delta.content or ""
            reasoning_delta = self._reasoning_text(delta)
            finish_reason = self._convert_finish_reason(choice.finish_reason)
            completed_calls: list[ToolCall] = []

            if finish_reason is not None and tool_calls:
                completed_calls = self._complete_tool_calls(tool_calls)
                emitted_tool_calls = True

            if (
                text_delta
                or reasoning_delta
                or completed_calls
                or usage is not None
                or finish_reason is not None
            ):
                yield LLMStreamChunk(
                    text_delta=text_delta,
                    reasoning_delta=reasoning_delta,
                    tool_calls=completed_calls,
                    usage=usage,
                    finish_reason=finish_reason,
                )

        if tool_calls and not emitted_tool_calls:
            yield LLMStreamChunk(
                tool_calls=self._complete_tool_calls(tool_calls),
                finish_reason="tool_call",
            )

    def _request_params(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": [self._convert_message(message) for message in messages],
        }
        if tools:
            params["tools"] = [self._convert_tool(tool) for tool in tools]
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            params["reasoning_effort"] = self.reasoning_effort.value
        return params

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        self._client.close()
        await self._async_client.close()

    def _convert_message(self, message: Message) -> dict[str, Any]:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("OpenAI tool message requires tool_call_id")
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": self._text_content(message),
            }

        if message.role == "assistant":
            result: dict[str, Any] = {
                "role": "assistant",
                "content": self._text_content(message) or None,
            }
            if message.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(
                                tool_call.arguments,
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            return result

        has_image = any(isinstance(part, ImagePart) for part in message.content)
        if has_image and message.role != "user":
            raise ValueError("OpenAI image input is only supported in user messages")

        if not has_image:
            return {
                "role": message.role,
                "content": self._text_content(message),
            }

        content: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            else:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": self._image_url(part)},
                    }
                )
        return {"role": message.role, "content": content}

    def _image_url(self, image: ImagePart) -> str:
        if image.source_type == "url":
            return image.source
        if self._image_resolver is None:
            raise ImageAssetUnavailableError(
                "image asset resolver is unavailable"
            )
        path = self._image_resolver(image)
        if image.media_type is None:
            raise ImageAssetUnavailableError(
                "image asset media type is unavailable"
            )
        media_type = image.media_type
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as error:
            raise ImageAssetUnavailableError(
                "image asset is unavailable"
            ) from error
        return f"data:{media_type};base64,{encoded}"

    @staticmethod
    def _text_content(message: Message) -> str:
        texts: list[str] = []
        for part in message.content:
            if not isinstance(part, TextPart):
                raise ValueError(f"{message.role} message only supports text content")
            texts.append(part.text)
        return "".join(texts)

    @staticmethod
    def _convert_tool(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def _convert_response(self, response: Any) -> LLMResponse:
        if not response.choices:
            raise ValueError("OpenAI response does not contain any choices")

        choice = response.choices[0]
        response_message = choice.message
        tool_calls = [
            ToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments=self._parse_arguments(tool_call.function.arguments),
            )
            for tool_call in (response_message.tool_calls or [])
        ]
        content = (
            [TextPart(response_message.content)]
            if response_message.content
            else []
        )
        message = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )
        return LLMResponse(
            message=message,
            reasoning=self._reasoning_text(response_message) or None,
            usage=self._convert_usage(response.usage),
            finish_reason=self._convert_finish_reason(choice.finish_reason),
        )

    @staticmethod
    def _collect_tool_call_deltas(
        deltas: Any,
        tool_calls: dict[int, dict[str, str]],
    ) -> None:
        for delta in deltas or []:
            state = tool_calls.setdefault(
                delta.index,
                {"id": "", "name": "", "arguments": ""},
            )
            if delta.id:
                state["id"] = delta.id
            if delta.function is not None:
                if delta.function.name:
                    state["name"] = delta.function.name
                if delta.function.arguments:
                    state["arguments"] += delta.function.arguments

    @classmethod
    def _complete_tool_calls(
        cls,
        tool_calls: dict[int, dict[str, str]],
    ) -> list[ToolCall]:
        return [
            ToolCall(
                id=state["id"],
                name=state["name"],
                arguments=cls._parse_arguments(state["arguments"]),
            )
            for _, state in sorted(tool_calls.items())
        ]

    @staticmethod
    def _parse_arguments(arguments: str) -> dict[str, Any]:
        if not arguments:
            return {}
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool call arguments must be a JSON object")
        return parsed

    @staticmethod
    def _reasoning_text(value: Any) -> str:
        return (
            getattr(value, "reasoning_content", None)
            or getattr(value, "reasoning", None)
            or ""
        )

    @staticmethod
    def _convert_usage(usage: Any) -> Usage | None:
        if usage is None:
            return None
        return Usage(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
        )

    @staticmethod
    def _convert_finish_reason(reason: str | None) -> FinishReason | None:
        if reason is None:
            return None
        return {
            "stop": "stop",
            "length": "length",
            "tool_calls": "tool_call",
            "function_call": "tool_call",
            "content_filter": "content_filter",
        }.get(reason, "other")
