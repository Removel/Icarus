"""工具执行入口。"""

from abc import ABC, abstractmethod
import asyncio
from concurrent.futures import ThreadPoolExecutor
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
    def execute_many(self, tool_calls: list[ToolCall]) -> list[ToolResultPair]:
        ...

    @abstractmethod
    async def aexecute_many(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ToolResultPair]:
        ...


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

    def execute_many(self, tool_calls: list[ToolCall]) -> list[ToolResultPair]:
        if not tool_calls:
            return []
        with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
            results = list(executor.map(self.execute, tool_calls))
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
