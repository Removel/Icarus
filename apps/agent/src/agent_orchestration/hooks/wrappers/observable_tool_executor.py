"""ToolExecutor 的透明观测包装器。"""

from uuid import uuid4

from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.agent_orchestration.tools.tool_executor import (
    BaseToolExecutor,
)
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolCall, ToolDefinition


_OBSERVABLE_EXECUTION_FIELDS = frozenset(
    {
        "disposition",
        "requested_timeout_seconds",
        "requested_output_tokens",
        "effective_timeout_seconds",
        "effective_output_tokens",
        "preview_tokens",
        "duration_seconds",
        "original_tokens",
        "visible_tokens",
        "original_bytes",
        "saved_bytes",
        "result_truncated",
        "result_file",
        "result_file_complete",
        "result_file_error",
        "context_budget_exhausted",
        "cancellation_confirmed",
        "output_capture_incomplete",
        "captured_output_bytes",
    }
)


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

    def snapshot(self, names: list[str] | None = None) -> BaseToolExecutor:
        return ObservableToolExecutor(
            self._executor.snapshot(names),
            self._dispatcher,
        )

    def can_run_parallel(self, tool_call: ToolCall) -> bool:
        return self._executor.can_run_parallel(tool_call)

    def prepare_group(self, tool_calls):
        return self._executor.prepare_group(tool_calls)

    def finalize_group(self, tool_calls, results_by_id, **execution):
        try:
            finalized = self._executor.finalize_group(
                tool_calls, results_by_id, **execution
            )
        except Exception as error:
            self._dispatcher.trigger(
                "tool.group",
                "error",
                {
                    "tool_call_ids": [call.id for call in tool_calls],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                },
            )
            raise
        self._dispatcher.trigger(
            "tool.group",
            "after",
            {
                "tool_call_ids": [call.id for call in tool_calls],
                "results": {
                    call.id: self._result_observation(finalized[call.id])
                    for call in tool_calls
                },
                "original_tokens": sum(
                    int(finalized[call.id].metadata.get("original_tokens", 0))
                    for call in tool_calls
                ),
                "visible_tokens": sum(
                    int(finalized[call.id].metadata.get("visible_tokens", 0))
                    for call in tool_calls
                ),
            },
        )
        return finalized

    def execute(
        self,
        tool_call: ToolCall,
        **execution: object,
    ) -> ToolExecutionResult:
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
            result = self._executor.execute(tool_call, **execution)
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
                "result": self._result_observation(result),
            },
        )
        return result

    async def aexecute(
        self,
        tool_call: ToolCall,
        **execution: object,
    ) -> ToolExecutionResult:
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
            result = await self._executor.aexecute(tool_call, **execution)
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
                "result": self._result_observation(result),
            },
        )
        return result

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

    @staticmethod
    def _result_observation(result: ToolExecutionResult) -> dict[str, object]:
        return {
            "success": result.success,
            "has_output": result.output is not None,
            "has_error": result.error is not None,
            "image_count": len(result.images),
            "execution": {
                key: value for key, value in result.metadata.items()
                if key in _OBSERVABLE_EXECUTION_FIELDS
            },
        }
