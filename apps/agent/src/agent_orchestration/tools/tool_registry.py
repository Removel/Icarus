"""工具注册中心。"""

import logging
from collections.abc import Iterable

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.tool_checker import ToolChecker
from apps.agent.src.model_provider.types import ToolDefinition


logger = logging.getLogger(__name__)


class ToolRegistry:
    """保存当前应用中检查通过的可用工具。"""

    def __init__(self, checker: ToolChecker | None = None) -> None:
        self._checker = checker or ToolChecker()
        self._tools: dict[str, BaseTool] = {}
        self._frozen = False

    def register(self, tool: BaseTool) -> bool:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")
        check_result = self._checker.check(tool)
        if not check_result.valid:
            logger.error(
                "Skip invalid tool: type=%s errors=%s",
                type(tool).__name__,
                "; ".join(check_result.errors),
            )
            return False

        name = tool.definition.name
        if name in self._tools:
            logger.error("Skip duplicate tool: name=%s", name)
            return False

        self._tools[name] = tool
        return True

    def unregister(self, name: str) -> BaseTool:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")
        try:
            return self._tools.pop(name)
        except KeyError as error:
            raise KeyError(f"Tool is not registered: {name}") from error

    def freeze(self) -> None:
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def register_many(self, tools: Iterable[BaseTool]) -> list[str]:
        registered: list[str] = []
        for tool in tools:
            if self.register(tool):
                registered.append(tool.definition.name)
        return registered

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def select(self, names: list[str] | None = None) -> list[BaseTool]:
        if names is None:
            return list(self._tools.values())

        selected: list[BaseTool] = []
        for name in names:
            tool = self.get(name)
            if tool is None:
                logger.error("Tool is not registered: name=%s", name)
                continue
            selected.append(tool)
        return selected

    def definitions(
        self,
        names: list[str] | None = None,
    ) -> list[ToolDefinition]:
        return [tool.definition for tool in self.select(names)]

    def names(self) -> list[str]:
        return list(self._tools)
