"""BaseLLM 的透明观测包装器。"""

from collections.abc import AsyncIterator, Iterator
import traceback
import time
from uuid import uuid4

from icarus.core.hooks.hook_dispatcher import (
    HookDispatcher,
)
from icarus.core.hooks.values import LLMCallValue, preserve_tool_set, preserve_response_control
from icarus.core.llm import BaseLLM
from icarus.core.models.messages import (
    LLMResponse,
    LLMStreamChunk,
    Message,
    ToolDefinition,
)


class ObservableLLM(BaseLLM):
    """在不修改模型厂商实现的前提下观测调用边界。"""

    def __init__(self, llm: BaseLLM, dispatcher: HookDispatcher) -> None:
        self._llm = llm
        self._dispatcher = dispatcher
        for name in ("llm.invoke", "llm.stream"):
            dispatcher.declare_point(name, "before", LLMCallValue, validator=preserve_tool_set)
        dispatcher.declare_point("llm.invoke", "after", LLMResponse, validator=preserve_response_control)

    def invoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        llm_call_id = uuid4().hex
        started_at = time.monotonic()
        data = self._input_data(llm_call_id, messages, tools)
        value = self._dispatcher.trigger("llm.invoke", "before", LLMCallValue(messages, tools), data=data)
        messages, tools = value.messages, value.tools
        try:
            response = self._llm.invoke(messages, tools)
        except Exception as error:
            self._dispatcher.record(
                "llm.invoke",
                "error",
                self._error_data(llm_call_id, error),
            )
            raise
        response = self._dispatcher.trigger(
            "llm.invoke", "after", response,
            data={"llm_call_id": llm_call_id, "usage": response.usage,
                  "finish_reason": response.finish_reason, "duration_seconds": time.monotonic() - started_at,
                  "text_chars": sum(len(getattr(part, "text", "")) for part in response.message.content)},
        )
        return response

    async def ainvoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        llm_call_id = uuid4().hex
        started_at = time.monotonic()
        data = self._input_data(llm_call_id, messages, tools)
        value = await self._dispatcher.atrigger("llm.invoke", "before", LLMCallValue(messages, tools), data=data)
        messages, tools = value.messages, value.tools
        try:
            response = await self._llm.ainvoke(messages, tools)
        except Exception as error:
            await self._dispatcher.arecord(
                "llm.invoke",
                "error",
                self._error_data(llm_call_id, error),
            )
            raise
        response = await self._dispatcher.atrigger(
            "llm.invoke", "after", response,
            data={"llm_call_id": llm_call_id, "usage": response.usage,
                  "finish_reason": response.finish_reason, "duration_seconds": time.monotonic() - started_at,
                  "text_chars": sum(len(getattr(part, "text", "")) for part in response.message.content)},
        )
        return response

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        llm_call_id = uuid4().hex
        started_at = time.monotonic()
        value = self._dispatcher.trigger(
            "llm.stream", "before", LLMCallValue(messages, tools),
            data={"llm_call_id": llm_call_id},
        )
        messages, tools = value.messages, value.tools
        chunks: list[LLMStreamChunk] = []
        try:
            for chunk in self._llm.stream(messages, tools):
                chunks.append(chunk)
                yield chunk
        except BaseException as error:
            self._dispatcher.record(
                "llm.stream",
                "error",
                self._base_error_data(llm_call_id, error),
            )
            raise
        self._dispatcher.record(
            "llm.stream",
            "after",
            {
                "llm_call_id": llm_call_id,
                "stream_result": self._aggregate_chunks(chunks),
                "finish_reason": next((c.finish_reason for c in reversed(chunks) if c.finish_reason is not None), None),
                "duration_seconds": time.monotonic() - started_at,
            },
        )

    async def astream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        llm_call_id = uuid4().hex
        started_at = time.monotonic()
        value = await self._dispatcher.atrigger(
            "llm.stream", "before", LLMCallValue(messages, tools),
            data={"llm_call_id": llm_call_id},
        )
        messages, tools = value.messages, value.tools
        chunks: list[LLMStreamChunk] = []
        try:
            async for chunk in self._llm.astream(messages, tools):
                chunks.append(chunk)
                yield chunk
        except BaseException as error:
            await self._dispatcher.arecord(
                "llm.stream",
                "error",
                self._base_error_data(llm_call_id, error),
            )
            raise
        await self._dispatcher.arecord(
            "llm.stream",
            "after",
            {
                "llm_call_id": llm_call_id,
                "stream_result": self._aggregate_chunks(chunks),
                "finish_reason": next((c.finish_reason for c in reversed(chunks) if c.finish_reason is not None), None),
                "duration_seconds": time.monotonic() - started_at,
            },
        )

    def close(self) -> None:
        self._llm.close()

    async def aclose(self) -> None:
        await self._llm.aclose()

    @staticmethod
    def _input_data(
        llm_call_id: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
    ) -> dict[str, object]:
        return {
            "llm_call_id": llm_call_id,
            "messages": messages,
            "tools": tools or [],
        }

    @staticmethod
    def _error_data(llm_call_id: str, error: Exception) -> dict[str, str]:
        return {
            "llm_call_id": llm_call_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        }

    @staticmethod
    def _base_error_data(
        llm_call_id: str,
        error: BaseException,
    ) -> dict[str, str]:
        return {
            "llm_call_id": llm_call_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        }

    @staticmethod
    def _aggregate_chunks(chunks: list[LLMStreamChunk]) -> dict[str, object]:
        return {
            "text": "".join(chunk.text_delta for chunk in chunks),
            "reasoning": "".join(chunk.reasoning_delta for chunk in chunks),
            "tool_calls": [
                tool_call
                for chunk in chunks
                for tool_call in chunk.tool_calls
            ],
            "usage": next(
                (
                    chunk.usage
                    for chunk in reversed(chunks)
                    if chunk.usage is not None
                ),
                None,
            ),
            "finish_reason": next(
                (
                    chunk.finish_reason
                    for chunk in reversed(chunks)
                    if chunk.finish_reason is not None
                ),
                None,
            ),
        }
