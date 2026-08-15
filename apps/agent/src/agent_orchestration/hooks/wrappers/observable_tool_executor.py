"""ToolExecutor 的透明观测包装器。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from uuid import uuid4

from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.agent_orchestration.tools.tool_executor import (
    BaseToolExecutor,
    ToolResultPair,
)
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolCall, ToolDefinition


class ObservableToolExecutor(BaseToolExecutor):
    """观测每个 ToolCall，同时保持 ToolExecutor 对外语义。"""

    def __init__(
        self,
        executor: BaseToolExecutor,
        dispatcher: HookDispatcher,
    ) -> None:
        self._executor = executor
        self._dispatcher = dispatcher

    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        return self._executor.definitions(names)

    def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        tool_execution_id = uuid4().hex
        self._dispatcher.trigger(
            "tool.execute",
            "before",
            {
                "tool_execution_id": tool_execution_id,
                "tool_call": tool_call,
            },
        )
        try:
            result = self._executor.execute(tool_call)
        except Exception as error:
            self._dispatcher.trigger(
                "tool.execute",
                "error",
                self._error_data(tool_execution_id, tool_call, error),
            )
            raise
        self._dispatcher.trigger(
            "tool.execute",
            "after",
            {
                "tool_execution_id": tool_execution_id,
                "tool_call": tool_call,
                "result": result,
            },
        )
        return result

    async def aexecute(self, tool_call: ToolCall) -> ToolExecutionResult:
        tool_execution_id = uuid4().hex
        await self._dispatcher.atrigger(
            "tool.execute",
            "before",
            {
                "tool_execution_id": tool_execution_id,
                "tool_call": tool_call,
            },
        )
        try:
            result = await self._executor.aexecute(tool_call)
        except Exception as error:
            await self._dispatcher.atrigger(
                "tool.execute",
                "error",
                self._error_data(tool_execution_id, tool_call, error),
            )
            raise
        await self._dispatcher.atrigger(
            "tool.execute",
            "after",
            {
                "tool_execution_id": tool_execution_id,
                "tool_call": tool_call,
                "result": result,
            },
        )
        return result

    def execute_many(self, tool_calls: list[ToolCall]) -> list[ToolResultPair]:
        if not tool_calls:
            return []
        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = [
                executor.submit(copy_context().run, self.execute, tool_call)
                for tool_call in tool_calls
            ]
            results = [future.result() for future in futures]
        return list(zip(tool_calls, results, strict=True))

    async def aexecute_many(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ToolResultPair]:
        results = await asyncio.gather(
            *(self.aexecute(tool_call) for tool_call in tool_calls),
        )
        return list(zip(tool_calls, results, strict=True))

    @staticmethod
    def _error_data(
        tool_execution_id: str,
        tool_call: ToolCall,
        error: Exception,
    ) -> dict[str, object]:
        return {
            "tool_execution_id": tool_execution_id,
            "tool_call": tool_call,
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
