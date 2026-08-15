"""无状态 ReAct Agent。"""

import json

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.types import AgentResponse
from apps.agent.src.agent_orchestration.tools.tool_executor import BaseToolExecutor
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    ImagePart,
    LLMResponse,
    Message,
    TextPart,
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
