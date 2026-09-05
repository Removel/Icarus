"""无状态 ReAct Agent。"""

from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from dataclasses import dataclass, field
import json

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.types import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.run_control.types import AgentRunControl
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


@dataclass
class _RunState:
    messages: list[Message]
    task_message_start: int
    tool_executor: BaseToolExecutor
    tool_definitions: list[ToolDefinition]
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    has_usage: bool = False
    last_usage: Usage | None = None
    reasoning_parts: list[str] = field(default_factory=list)
    steps: int = 0


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
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        state = self._create_run_state(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
        )

        while True:
            self._begin_step(state, run_control)
            response = self._llm.invoke(
                state.messages, state.tool_definitions or None
            )
            self._accept_response(state, response)

            if not response.message.tool_calls:
                if not self._close_or_continue(
                    state.messages, run_control, state.steps + 1
                ):
                    self._checkpoint_state(state, run_control)
                    continue
                self._checkpoint_state(state, run_control)
                return self._build_response(response, state)

            for batch in state.tool_executor.build_batches(
                response.message.tool_calls
            ):
                self._raise_if_cancelled(run_control)
                execution = self._tool_execution(
                    state.messages,
                    run_control,
                    state.steps,
                    state.task_message_start,
                )
                results_by_id = {
                    tool_call.id: result
                    for tool_call, result in state.tool_executor.iter_completed(
                        batch, **execution
                    )
                }
                for tool_call in batch:
                    state.messages.append(
                        self._tool_result_message(
                            tool_call.id,
                            results_by_id[tool_call.id],
                        )
                    )
            self._checkpoint_state(state, run_control)

    async def ainvoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        state = self._create_run_state(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
        )

        while True:
            self._begin_step(state, run_control)
            response = await self._llm.ainvoke(
                state.messages,
                state.tool_definitions or None,
            )
            self._accept_response(state, response)

            if not response.message.tool_calls:
                if not self._close_or_continue(
                    state.messages, run_control, state.steps + 1
                ):
                    self._checkpoint_state(state, run_control)
                    continue
                self._checkpoint_state(state, run_control)
                return self._build_response(response, state)

            for batch in state.tool_executor.build_batches(
                response.message.tool_calls
            ):
                self._raise_if_cancelled(run_control)
                execution = self._tool_execution(
                    state.messages,
                    run_control,
                    state.steps,
                    state.task_message_start,
                )
                results_by_id: dict[str, ToolExecutionResult] = {}
                async for tool_call, result in state.tool_executor.aiter_completed(
                    batch, **execution
                ):
                    results_by_id[tool_call.id] = result
                for tool_call in batch:
                    state.messages.append(
                        self._tool_result_message(
                            tool_call.id,
                            results_by_id[tool_call.id],
                        )
                    )
            self._checkpoint_state(state, run_control)

    def stream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> Iterator[Event]:
        state = self._create_run_state(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
        )

        while True:
            self._begin_step(state, run_control)
            chunks: list[LLMStreamChunk] = []
            for chunk in self._llm.stream(
                state.messages,
                state.tool_definitions or None,
            ):
                if chunk.text_delta:
                    yield AgentTextDeltaEvent(
                        step=state.steps,
                        text=chunk.text_delta,
                    )
                chunks.append(chunk)

            response = self._aggregate_stream_chunks(chunks)
            self._accept_response(state, response)
            yield AgentMessageCompletedEvent(
                step=state.steps,
                message=deepcopy(response.message),
            )

            if not response.message.tool_calls:
                if not self._close_or_continue(
                    state.messages,
                    run_control,
                    state.steps + 1,
                ):
                    self._checkpoint_state(state, run_control)
                    continue
                self._checkpoint_state(state, run_control)
                agent_response = self._build_response(response, state)
                yield AgentCompletedEvent(
                    step=state.steps,
                    response=agent_response,
                )
                return

            self._raise_if_cancelled(run_control)
            batches = state.tool_executor.build_batches(
                response.message.tool_calls
            )
            for batch_index, batch in enumerate(batches):
                self._raise_if_cancelled(run_control)
                for tool_call in batch:
                    yield AgentToolStartedEvent(
                        step=state.steps,
                        tool_call=tool_call,
                    )

                results_by_id: dict[str, ToolExecutionResult] = {}
                execution = self._tool_execution(
                    state.messages,
                    run_control,
                    state.steps,
                    state.task_message_start,
                )
                for tool_call, result in state.tool_executor.iter_completed(
                    batch, **execution
                ):
                    results_by_id[tool_call.id] = result
                    is_last_result = len(results_by_id) == len(batch)
                    is_last_batch = batch_index == len(batches) - 1
                    if is_last_result:
                        self._append_tool_results(
                            state.messages,
                            batch,
                            results_by_id,
                        )
                        if is_last_batch:
                            self._checkpoint_state(state, run_control)
                    yield AgentToolCompletedEvent(
                        step=state.steps,
                        tool_call=tool_call,
                        result=result,
                    )

    async def astream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AsyncIterator[Event]:
        state = self._create_run_state(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
            tools,
        )

        while True:
            self._begin_step(state, run_control)
            chunks: list[LLMStreamChunk] = []
            async for chunk in self._llm.astream(
                state.messages,
                state.tool_definitions or None,
            ):
                if chunk.text_delta:
                    yield AgentTextDeltaEvent(
                        step=state.steps,
                        text=chunk.text_delta,
                    )
                chunks.append(chunk)

            response = self._aggregate_stream_chunks(chunks)
            self._accept_response(state, response)
            yield AgentMessageCompletedEvent(
                step=state.steps,
                message=deepcopy(response.message),
            )

            if not response.message.tool_calls:
                if not self._close_or_continue(
                    state.messages,
                    run_control,
                    state.steps + 1,
                ):
                    self._checkpoint_state(state, run_control)
                    continue
                self._checkpoint_state(state, run_control)
                agent_response = self._build_response(response, state)
                yield AgentCompletedEvent(
                    step=state.steps,
                    response=agent_response,
                )
                return

            self._raise_if_cancelled(run_control)
            batches = state.tool_executor.build_batches(
                response.message.tool_calls
            )
            for batch_index, batch in enumerate(batches):
                self._raise_if_cancelled(run_control)
                for tool_call in batch:
                    yield AgentToolStartedEvent(
                        step=state.steps,
                        tool_call=tool_call,
                    )

                results_by_id: dict[str, ToolExecutionResult] = {}
                execution = self._tool_execution(
                    state.messages,
                    run_control,
                    state.steps,
                    state.task_message_start,
                )
                async for tool_call, result in state.tool_executor.aiter_completed(
                    batch, **execution
                ):
                    results_by_id[tool_call.id] = result
                    is_last_result = len(results_by_id) == len(batch)
                    is_last_batch = batch_index == len(batches) - 1
                    if is_last_result:
                        self._append_tool_results(
                            state.messages,
                            batch,
                            results_by_id,
                        )
                        if is_last_batch:
                            self._checkpoint_state(state, run_control)
                    yield AgentToolCompletedEvent(
                        step=state.steps,
                        tool_call=tool_call,
                        result=result,
                    )

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

    def _create_run_state(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None,
        tools: list[str] | None,
    ) -> _RunState:
        messages = self._build_messages(
            system_prompt,
            history_messages,
            input_prompt,
            input_images,
        )
        tool_executor = self._tool_executor.snapshot(tools)
        return _RunState(
            messages=messages,
            task_message_start=len(messages) - 1,
            tool_executor=tool_executor,
            tool_definitions=tool_executor.definitions(),
        )

    @classmethod
    def _begin_step(
        cls,
        state: _RunState,
        run_control: AgentRunControl | None,
    ) -> None:
        cls._prepare_step(
            state.messages,
            run_control,
            state.steps + 1,
            state.task_message_start,
            state.last_usage,
        )
        state.steps += 1
        cls._mark_step(run_control, state.steps)

    @staticmethod
    def _accept_response(state: _RunState, response: LLMResponse) -> None:
        state.messages.append(response.message)
        state.usage, state.has_usage = ReActAgent._add_usage(
            state.usage,
            state.has_usage,
            response,
        )
        state.last_usage = response.usage
        if response.reasoning:
            state.reasoning_parts.append(response.reasoning)

    @classmethod
    def _checkpoint_state(
        cls,
        state: _RunState,
        run_control: AgentRunControl | None,
    ) -> None:
        cls._checkpoint_history(
            state.messages,
            run_control,
            state.task_message_start,
            state.last_usage,
        )

    @staticmethod
    def _tool_execution(
        messages: list[Message],
        run_control: AgentRunControl | None,
        step: int,
        task_message_start: int,
    ) -> dict[str, object]:
        return {
            "task_id": (
                run_control.task_id if run_control is not None else None
            ),
            "run_id": (
                run_control.run_id if run_control is not None else None
            ),
            "step": step,
            "task_messages": tuple(
                deepcopy(messages[task_message_start:])
            ),
        }

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
                ),
                *result.images,
            ],
            tool_call_id=tool_call_id,
        )

    @classmethod
    def _append_tool_results(
        cls,
        messages: list[Message],
        tool_calls: list[ToolCall],
        results_by_id: dict[str, ToolExecutionResult],
    ) -> None:
        for tool_call in tool_calls:
            messages.append(
                cls._tool_result_message(
                    tool_call.id,
                    results_by_id[tool_call.id],
                )
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
        state: _RunState,
    ) -> AgentResponse:
        return AgentResponse(
            message=response.message,
            reasoning="\n".join(state.reasoning_parts) or None,
            usage=state.usage if state.has_usage else None,
            last_usage=state.last_usage,
            finish_reason=response.finish_reason,
            steps=state.steps,
            messages=list(state.messages),
            task_message_start=state.task_message_start,
        )

    @staticmethod
    def _prepare_step(
        messages: list[Message],
        run_control: AgentRunControl | None,
        step: int,
        task_message_start: int,
        last_usage: Usage | None,
    ) -> None:
        if run_control is None:
            return
        run_control.raise_if_cancelled()
        batch = run_control.drain_context(applied_before_step=step)
        if batch is not None:
            messages.append(batch.message)
        run_control.checkpoint_history(
            messages[task_message_start:],
            last_usage,
        )
        run_control.raise_if_step_exceeded(step)

    @staticmethod
    def _close_or_continue(
        messages: list[Message],
        run_control: AgentRunControl | None,
        next_step: int,
    ) -> bool:
        if run_control is None:
            return True
        batch = run_control.close_or_drain(applied_before_step=next_step)
        if batch is None:
            return True
        messages.append(batch.message)
        return False

    @staticmethod
    def _mark_step(run_control: AgentRunControl | None, step: int) -> None:
        if run_control is not None:
            run_control.mark_step(step)

    @staticmethod
    def _checkpoint_history(
        messages: list[Message],
        run_control: AgentRunControl | None,
        task_message_start: int,
        last_usage: Usage | None,
    ) -> None:
        if run_control is not None:
            run_control.checkpoint_history(
                messages[task_message_start:],
                last_usage,
            )

    @staticmethod
    def _raise_if_cancelled(run_control: AgentRunControl | None) -> None:
        if run_control is not None:
            run_control.raise_if_cancelled()

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
