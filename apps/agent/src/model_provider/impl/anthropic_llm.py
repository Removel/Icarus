import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from anthropic import Anthropic, AsyncAnthropic

from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    FinishReason,
    ImagePart,
    LLMResponse,
    LLMStreamChunk,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
    Usage,
)


class AnthropicLLM(BaseLLM):
    """使用 Anthropic Messages 协议完成单次 LLM 调用。"""

    def __init__(
        self,
        model_name: str,
        api_key: str,
        max_tokens: int,
        base_url: str | None = None,
        temperature: float | None = None,
        thinking_budget: int = 1024,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking_budget = thinking_budget
        self._client = Anthropic(api_key=api_key, base_url=base_url)
        self._async_client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    def invoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        response = self._client.messages.create(
            **self._request_params(messages, tools),
        )
        return self._convert_response(response)

    async def ainvoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        response = await self._async_client.messages.create(
            **self._request_params(messages, tools),
        )
        return self._convert_response(response)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        stream = self._client.messages.create(
            **self._request_params(messages, tools),
            stream=True,
        )
        tool_calls: dict[int, dict[str, str]] = {}
        input_tokens = 0

        for event in stream:
            if event.type == "message_start":
                input_tokens = event.message.usage.input_tokens
                continue

            if event.type == "content_block_start":
                block = event.content_block
                if block.type == "text" and block.text:
                    yield LLMStreamChunk(text_delta=block.text)
                elif block.type == "thinking" and block.thinking:
                    yield LLMStreamChunk(reasoning_delta=block.thinking)
                elif block.type == "tool_use":
                    tool_calls[event.index] = {
                        "id": block.id,
                        "name": block.name,
                        "arguments": (
                            json.dumps(block.input, ensure_ascii=False)
                            if block.input
                            else ""
                        ),
                    }
                continue

            if event.type == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    yield LLMStreamChunk(text_delta=delta.text)
                elif delta.type == "thinking_delta":
                    yield LLMStreamChunk(reasoning_delta=delta.thinking)
                elif delta.type == "input_json_delta":
                    tool_calls[event.index]["arguments"] += delta.partial_json
                continue

            if event.type == "content_block_stop":
                state = tool_calls.pop(event.index, None)
                if state is not None:
                    yield LLMStreamChunk(
                        tool_calls=[self._complete_tool_call(state)],
                    )
                continue

            if event.type == "message_delta":
                yield LLMStreamChunk(
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=event.usage.output_tokens,
                    ),
                    finish_reason=self._convert_finish_reason(
                        event.delta.stop_reason,
                    ),
                )

    async def astream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        stream = await self._async_client.messages.create(
            **self._request_params(messages, tools),
            stream=True,
        )
        tool_calls: dict[int, dict[str, str]] = {}
        input_tokens = 0

        async for event in stream:
            if event.type == "message_start":
                input_tokens = event.message.usage.input_tokens
                continue

            if event.type == "content_block_start":
                block = event.content_block
                if block.type == "text" and block.text:
                    yield LLMStreamChunk(text_delta=block.text)
                elif block.type == "thinking" and block.thinking:
                    yield LLMStreamChunk(reasoning_delta=block.thinking)
                elif block.type == "tool_use":
                    tool_calls[event.index] = {
                        "id": block.id,
                        "name": block.name,
                        "arguments": (
                            json.dumps(block.input, ensure_ascii=False)
                            if block.input
                            else ""
                        ),
                    }
                continue

            if event.type == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    yield LLMStreamChunk(text_delta=delta.text)
                elif delta.type == "thinking_delta":
                    yield LLMStreamChunk(reasoning_delta=delta.thinking)
                elif delta.type == "input_json_delta":
                    tool_calls[event.index]["arguments"] += delta.partial_json
                continue

            if event.type == "content_block_stop":
                state = tool_calls.pop(event.index, None)
                if state is not None:
                    yield LLMStreamChunk(
                        tool_calls=[self._complete_tool_call(state)],
                    )
                continue

            if event.type == "message_delta":
                yield LLMStreamChunk(
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=event.usage.output_tokens,
                    ),
                    finish_reason=self._convert_finish_reason(
                        event.delta.stop_reason,
                    ),
                )

    def _request_params(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        system, converted_messages = self._convert_messages(messages)
        params: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,
            "messages": converted_messages,
            "thinking": {
                "type": "enabled",
                "budget_tokens": self.thinking_budget,
            },
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        self._client.close()
        await self._async_client.close()

    def _convert_messages(
        self,
        messages: list[Message],
    ) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        converted_messages: list[dict[str, Any]] = []

        for message in messages:
            if message.role == "system":
                if message.tool_calls or message.tool_call_id:
                    raise ValueError("Anthropic system message cannot contain tool data")
                system_parts.append(self._text_content(message))
                continue

            converted = self._convert_message(message)
            if (
                converted_messages
                and converted_messages[-1]["role"] == converted["role"]
            ):
                converted_messages[-1]["content"].extend(converted["content"])
            else:
                converted_messages.append(converted)

        return "\n".join(system_parts), converted_messages

    def _convert_message(self, message: Message) -> dict[str, Any]:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("Anthropic tool message requires tool_call_id")
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": self._text_content(message),
                    }
                ],
            }

        content = self._convert_content(message)
        if message.role == "assistant":
            content.extend(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.arguments,
                }
                for tool_call in message.tool_calls
            )
        return {"role": message.role, "content": content}

    @staticmethod
    def _convert_content(message: Message) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif message.role == "user":
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": part.url,
                        },
                    }
                )
            else:
                raise ValueError(
                    f"Anthropic {message.role} message only supports text content",
                )
        return content

    @staticmethod
    def _text_content(message: Message) -> str:
        texts: list[str] = []
        for part in message.content:
            if isinstance(part, ImagePart):
                raise ValueError(f"{message.role} message only supports text content")
            texts.append(part.text)
        return "".join(texts)

    def _convert_response(self, response: Any) -> LLMResponse:
        content: list[TextPart] = []
        reasoning: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content.append(TextPart(block.text))
            elif block.type == "thinking":
                reasoning.append(block.thinking)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        return LLMResponse(
            message=Message(
                role="assistant",
                content=content,
                tool_calls=tool_calls,
            ),
            reasoning="".join(reasoning) or None,
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            finish_reason=self._convert_finish_reason(response.stop_reason),
        )

    @classmethod
    def _complete_tool_call(cls, state: dict[str, str]) -> ToolCall:
        return ToolCall(
            id=state["id"],
            name=state["name"],
            arguments=cls._parse_arguments(state["arguments"]),
        )

    @staticmethod
    def _parse_arguments(arguments: str) -> dict[str, Any]:
        if not arguments:
            return {}
        parsed = json.loads(arguments)
        if not isinstance(parsed, dict):
            raise ValueError("Tool call arguments must be a JSON object")
        return parsed

    @staticmethod
    def _convert_finish_reason(reason: str | None) -> FinishReason | None:
        if reason is None:
            return None
        return {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_call",
            "refusal": "content_filter",
        }.get(reason, "other")
