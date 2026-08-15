"""BaseLLM 的透明观测包装器。"""

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
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

    def invoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        llm_call_id = uuid4().hex
        data = self._input_data(llm_call_id, messages, tools)
        self._dispatcher.trigger("llm.invoke", "before", data)
        try:
            response = self._llm.invoke(messages, tools)
        except Exception as error:
            self._dispatcher.trigger(
                "llm.invoke",
                "error",
                self._error_data(llm_call_id, error),
            )
            raise
        self._dispatcher.trigger(
            "llm.invoke",
            "after",
            {"llm_call_id": llm_call_id, "response": response},
        )
        return response

    async def ainvoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        llm_call_id = uuid4().hex
        data = self._input_data(llm_call_id, messages, tools)
        await self._dispatcher.atrigger("llm.invoke", "before", data)
        try:
            response = await self._llm.ainvoke(messages, tools)
        except Exception as error:
            await self._dispatcher.atrigger(
                "llm.invoke",
                "error",
                self._error_data(llm_call_id, error),
            )
            raise
        await self._dispatcher.atrigger(
            "llm.invoke",
            "after",
            {"llm_call_id": llm_call_id, "response": response},
        )
        return response

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        llm_call_id = uuid4().hex
        self._dispatcher.trigger(
            "llm.stream",
            "before",
            self._input_data(llm_call_id, messages, tools),
        )
        chunks: list[LLMStreamChunk] = []
        try:
            for chunk in self._llm.stream(messages, tools):
                chunks.append(chunk)
                yield chunk
        except BaseException as error:
            self._dispatcher.trigger(
                "llm.stream",
                "error",
                self._base_error_data(llm_call_id, error),
            )
            raise
        self._dispatcher.trigger(
            "llm.stream",
            "after",
            {
                "llm_call_id": llm_call_id,
                "stream_result": self._aggregate_chunks(chunks),
            },
        )

    async def astream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        llm_call_id = uuid4().hex
        await self._dispatcher.atrigger(
            "llm.stream",
            "before",
            self._input_data(llm_call_id, messages, tools),
        )
        chunks: list[LLMStreamChunk] = []
        try:
            async for chunk in self._llm.astream(messages, tools):
                chunks.append(chunk)
                yield chunk
        except BaseException as error:
            await self._dispatcher.atrigger(
                "llm.stream",
                "error",
                self._base_error_data(llm_call_id, error),
            )
            raise
        await self._dispatcher.atrigger(
            "llm.stream",
            "after",
            {
                "llm_call_id": llm_call_id,
                "stream_result": self._aggregate_chunks(chunks),
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
