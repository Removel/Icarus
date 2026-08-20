"""工具执行入口。"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
import logging

from apps.agent.src.agent_orchestration.tools.tool_registry import ToolRegistry
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolCall, ToolDefinition


logger = logging.getLogger(__name__)
ToolResultPair = tuple[ToolCall, ToolExecutionResult]


class BaseToolExecutor(ABC):
    """Agent 能力层依赖的工具执行契约。"""

    @abstractmethod
    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        ...

    @abstractmethod
    def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        ...

    @abstractmethod
    async def aexecute(self, tool_call: ToolCall) -> ToolExecutionResult:
        ...

    @abstractmethod
    def can_run_parallel(self, tool_call: ToolCall) -> bool:
        ...

    def build_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        batches: list[list[ToolCall]] = []
        parallel_batch: list[ToolCall] = []

        for tool_call in tool_calls:
            if self.can_run_parallel(tool_call):
                parallel_batch.append(tool_call)
                continue

            if parallel_batch:
                batches.append(parallel_batch)
                parallel_batch = []
            batches.append([tool_call])

        if parallel_batch:
            batches.append(parallel_batch)
        return batches

    def iter_completed(
        self,
        tool_calls: list[ToolCall],
    ) -> Iterator[ToolResultPair]:
        if len(tool_calls) <= 1:
            for tool_call in tool_calls:
                yield tool_call, self.execute(tool_call)
            return

        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            futures = {
                executor.submit(copy_context().run, self.execute, tool_call): tool_call
                for tool_call in tool_calls
            }
            for future in as_completed(futures):
                yield futures[future], future.result()

    async def aiter_completed(
        self,
        tool_calls: list[ToolCall],
    ) -> AsyncIterator[ToolResultPair]:
        async def execute_pair(tool_call: ToolCall) -> ToolResultPair:
            return tool_call, await self.aexecute(tool_call)

        tasks = [
            asyncio.create_task(execute_pair(tool_call))
            for tool_call in tool_calls
        ]
        try:
            for task in asyncio.as_completed(tasks):
                yield await task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def execute_many(self, tool_calls: list[ToolCall]) -> list[ToolResultPair]:
        ordered_results: list[ToolResultPair] = []
        for batch in self.build_batches(tool_calls):
            results_by_id = {
                tool_call.id: result
                for tool_call, result in self.iter_completed(batch)
            }
            ordered_results.extend(
                (tool_call, results_by_id[tool_call.id])
                for tool_call in batch
            )
        return ordered_results

    async def aexecute_many(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ToolResultPair]:
        ordered_results: list[ToolResultPair] = []
        for batch in self.build_batches(tool_calls):
            results_by_id: dict[str, ToolExecutionResult] = {}
            async for tool_call, result in self.aiter_completed(batch):
                results_by_id[tool_call.id] = result
            ordered_results.extend(
                (tool_call, results_by_id[tool_call.id])
                for tool_call in batch
            )
        return ordered_results


class ToolExecutor(BaseToolExecutor):
    """查找并执行工具，将所有路径统一为 ToolExecutionResult。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        return self.registry.definitions(names)

    def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return ToolExecutionResult(
                success=False,
                error=f"Tool is not registered: {tool_call.name}",
            )

        try:
            result = tool.invoke(tool_call.arguments)
        except Exception as error:
            logger.exception(
                "Tool execution failed: name=%s call_id=%s",
                tool_call.name,
                tool_call.id,
            )
            return ToolExecutionResult(success=False, error=str(error))

        return self._normalize_result(tool_call, result)

    async def aexecute(self, tool_call: ToolCall) -> ToolExecutionResult:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return ToolExecutionResult(
                success=False,
                error=f"Tool is not registered: {tool_call.name}",
            )

        try:
            result = await tool.ainvoke(tool_call.arguments)
        except Exception as error:
            logger.exception(
                "Async tool execution failed: name=%s call_id=%s",
                tool_call.name,
                tool_call.id,
            )
            return ToolExecutionResult(success=False, error=str(error))

        return self._normalize_result(tool_call, result)

    def can_run_parallel(self, tool_call: ToolCall) -> bool:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return False
        return tool.can_run_parallel(tool_call.arguments)

    @staticmethod
    def _normalize_result(
        tool_call: ToolCall,
        result: object,
    ) -> ToolExecutionResult:
        if isinstance(result, ToolExecutionResult):
            return result
        return ToolExecutionResult(
            success=False,
            error=(
                f"Tool returned invalid result: name={tool_call.name} "
                f"type={type(result).__name__}"
            ),
        )
