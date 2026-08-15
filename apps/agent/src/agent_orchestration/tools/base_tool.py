"""可执行工具抽象。"""

from abc import ABC, abstractmethod
import asyncio
from typing import Any

from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class BaseTool(ABC):
    """所有 Agent 工具需要实现的统一接口。"""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @abstractmethod
    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        ...

    async def ainvoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        return await asyncio.to_thread(self.invoke, arguments)
