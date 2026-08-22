"""无状态 ReAct Agent。"""

from collections.abc import AsyncIterator, Iterator
import json

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.types import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.tools.tool_executor import BaseToolExecutor
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    ImagePart,
    LLMResponse,
    LLMStreamChunk,
    Message,
    TextPart,
    ToolCall,
    ToolDefinition,
    Usage,
)


class ReActAgent(BaseAgent):
    """执行 LLM、工具与上下文回填的通用 ReAct 循环。"""

    def __init__(
        self,
        model_role: LLMRole,
        llm: BaseLLM,
        tool_executor: BaseToolExecutor,
    ) -> None:
        self._model_role = model_role
        self._llm = llm
        self._tool_executor = tool_executor

    @property
    def model_role(self) -> LLMRole:
        return self._model_role

    def invoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AgentResponse:
        messages = self._build_messages(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
        )
        tool_definitions = self._tool_executor.definitions(tools)
        usage = Usage(input_tokens=0, output_tokens=0)
        has_usage = False
        reasoning_parts: list[str] = []
        steps = 0

        while True:
            response = self._llm.invoke(messages, tool_definitions or None)
            steps += 1
            messages.append(response.message)
            usage, has_usage = self._add_usage(usage, has_usage, response)
            if response.reasoning:
                reasoning_parts.append(response.reasoning)

            if not response.message.tool_calls:
                return self._build_response(
                    response,
                    messages,
                    reasoning_parts,
                    usage if has_usage else None,
                    steps,
                )

            for tool_call, result in self._tool_executor.execute_many(
                response.message.tool_calls,
            ):
                messages.append(self._tool_result_message(tool_call.id, result))

    async def ainvoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AgentResponse:
        messages = self._build_messages(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
        )
        tool_definitions = self._tool_executor.definitions(tools)
        usage = Usage(input_tokens=0, output_tokens=0)
        has_usage = False
        reasoning_parts: list[str] = []
        steps = 0

        while True:
            response = await self._llm.ainvoke(
                messages,
                tool_definitions or None,
            )
            steps += 1
            messages.append(response.message)
            usage, has_usage = self._add_usage(usage, has_usage, response)
            if response.reasoning:
                reasoning_parts.append(response.reasoning)

            if not response.message.tool_calls:
                return self._build_response(
                    response,
                    messages,
                    reasoning_parts,
                    usage if has_usage else None,
                    steps,
                )

            results = await self._tool_executor.aexecute_many(
                response.message.tool_calls,
            )
            for tool_call, result in results:
                messages.append(self._tool_result_message(tool_call.id, result))

    def stream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> Iterator[Event]:
        messages = self._build_messages(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
        )
        tool_definitions = self._tool_executor.definitions(tools)
        usage = Usage(input_tokens=0, output_tokens=0)
        has_usage = False
        reasoning_parts: list[str] = []
        steps = 0

        try:
            while True:
                steps += 1
                chunks: list[LLMStreamChunk] = []
                for chunk in self._llm.stream(
                    messages,
                    tool_definitions or None,
                ):
                    if chunk.text_delta:
                        yield AgentTextDeltaEvent(
                            step=steps,
                            text=chunk.text_delta,
                        )
                    chunks.append(chunk)

                response = self._aggregate_stream_chunks(chunks)
                messages.append(response.message)
                usage, has_usage = self._add_usage(usage, has_usage, response)
                if response.reasoning:
                    reasoning_parts.append(response.reasoning)

                if not response.message.tool_calls:
                    agent_response = self._build_response(
                        response,
                        messages,
                        reasoning_parts,
                        usage if has_usage else None,
                        steps,
                    )
                    yield AgentCompletedEvent(
                        step=steps,
                        response=agent_response,
                    )
                    return

                for batch in self._tool_executor.build_batches(
                    response.message.tool_calls,
                ):
                    for tool_call in batch:
                        yield AgentToolStartedEvent(
                            step=steps,
                            tool_call=tool_call,
                        )

                    results_by_id: dict[str, ToolExecutionResult] = {}
                    for tool_call, result in self._tool_executor.iter_completed(batch):
                        results_by_id[tool_call.id] = result
                        yield AgentToolCompletedEvent(
                            step=steps,
                            tool_call=tool_call,
                            result=result,
                        )

                    for tool_call in batch:
                        messages.append(
                            self._tool_result_message(
                                tool_call.id,
                                results_by_id[tool_call.id],
                            )
                        )
        except Exception as error:
            yield AgentErrorEvent(
                step=steps,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

    async def astream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
    ) -> AsyncIterator[Event]:
        messages = self._build_messages(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
        )
        tool_definitions = self._tool_executor.definitions(tools)
        usage = Usage(input_tokens=0, output_tokens=0)
        has_usage = False
        reasoning_parts: list[str] = []
        steps = 0

        try:
            while True:
                steps += 1
                chunks: list[LLMStreamChunk] = []
                async for chunk in self._llm.astream(
                    messages,
                    tool_definitions or None,
                ):
                    if chunk.text_delta:
                        yield AgentTextDeltaEvent(
                            step=steps,
                            text=chunk.text_delta,
                        )
                    chunks.append(chunk)

                response = self._aggregate_stream_chunks(chunks)
                messages.append(response.message)
                usage, has_usage = self._add_usage(usage, has_usage, response)
                if response.reasoning:
                    reasoning_parts.append(response.reasoning)

                if not response.message.tool_calls:
                    agent_response = self._build_response(
                        response,
                        messages,
                        reasoning_parts,
                        usage if has_usage else None,
                        steps,
                    )
                    yield AgentCompletedEvent(
                        step=steps,
                        response=agent_response,
                    )
                    return

                for batch in self._tool_executor.build_batches(
                    response.message.tool_calls,
                ):
                    for tool_call in batch:
                        yield AgentToolStartedEvent(
                            step=steps,
                            tool_call=tool_call,
                        )

                    results_by_id: dict[str, ToolExecutionResult] = {}
                    async for tool_call, result in self._tool_executor.aiter_completed(
                        batch
                    ):
                        results_by_id[tool_call.id] = result
                        yield AgentToolCompletedEvent(
                            step=steps,
                            tool_call=tool_call,
                            result=result,
                        )

                    for tool_call in batch:
                        messages.append(
                            self._tool_result_message(
                                tool_call.id,
                                results_by_id[tool_call.id],
                            )
                        )
        except Exception as error:
            yield AgentErrorEvent(
                step=steps,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise

    @staticmethod
    def _build_messages(
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None,
    ) -> list[Message]:
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message("system", [TextPart(system_prompt)]))
        messages.extend(history_messages)
        content = [TextPart(input_prompt), *(input_images or [])]
        messages.append(Message("user", content))
        return messages

    @staticmethod
    def _tool_result_message(
        tool_call_id: str,
        result: ToolExecutionResult,
    ) -> Message:
        return Message(
            role="tool",
            content=[
                TextPart(
                    json.dumps(
                        result.as_dict(),
                        ensure_ascii=False,
                        default=str,
                    )
                )
            ],
            tool_call_id=tool_call_id,
        )

    @staticmethod
    def _add_usage(
        current: Usage,
        has_usage: bool,
        response: LLMResponse,
    ) -> tuple[Usage, bool]:
        if response.usage is None:
            return current, has_usage
        return (
            Usage(
                input_tokens=current.input_tokens + response.usage.input_tokens,
                output_tokens=current.output_tokens + response.usage.output_tokens,
            ),
            True,
        )

    @staticmethod
    def _build_response(
        response: LLMResponse,
        messages: list[Message],
        reasoning_parts: list[str],
        usage: Usage | None,
        steps: int,
    ) -> AgentResponse:
        return AgentResponse(
            message=response.message,
            reasoning="\n".join(reasoning_parts) or None,
            usage=usage,
            finish_reason=response.finish_reason,
            steps=steps,
            messages=list(messages),
        )

    @classmethod
    def _aggregate_stream_chunks(
        cls,
        chunks: list[LLMStreamChunk],
    ) -> LLMResponse:
        if not chunks:
            raise ValueError("LLM stream did not produce any chunks")

        text = "".join(chunk.text_delta for chunk in chunks)
        reasoning = "".join(chunk.reasoning_delta for chunk in chunks)
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextPart(text)] if text else [],
                tool_calls=[
                    tool_call
                    for chunk in chunks
                    for tool_call in chunk.tool_calls
                ],
            ),
            reasoning=reasoning or None,
            usage=next(
                (
                    chunk.usage
                    for chunk in reversed(chunks)
                    if chunk.usage is not None
                ),
                None,
            ),
            finish_reason=next(
                (
                    chunk.finish_reason
                    for chunk in reversed(chunks)
                    if chunk.finish_reason is not None
                ),
                None,
            ),
        )
