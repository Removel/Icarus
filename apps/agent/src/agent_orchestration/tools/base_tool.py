"""可执行工具抽象。"""

from abc import ABC, abstractmethod
import asyncio
from typing import Any

from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolDefinition


class BaseTool(ABC):
    """所有 Agent 工具需要实现的统一接口。"""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        ...

    @abstractmethod
    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        ...

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        return await asyncio.to_thread(
            self.invoke,
            arguments,
            task_id=task_id,
            run_id=run_id,
            step=step,
            task_messages=task_messages,
        )

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return False
