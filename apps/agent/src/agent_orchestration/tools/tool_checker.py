"""工具注册前的形式检查。"""

from dataclasses import dataclass, field
import inspect
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.model_provider.types import ToolDefinition


@dataclass(frozen=True)
class ToolCheckResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class ToolChecker:
    """检查工具是否满足编排层的最小契约。"""

    def check(self, tool: Any) -> ToolCheckResult:
        errors: list[str] = []
        if not isinstance(tool, BaseTool):
            errors.append("tool must inherit BaseTool")
            return ToolCheckResult(valid=False, errors=errors)

        try:
            definition = tool.definition
        except Exception as error:
            errors.append(f"failed to read tool definition: {error}")
            return ToolCheckResult(valid=False, errors=errors)

        if not isinstance(definition, ToolDefinition):
            errors.append("definition must be ToolDefinition")
        else:
            if not definition.name.strip():
                errors.append("tool name cannot be empty")
            if not definition.description.strip():
                errors.append("tool description cannot be empty")
            if not self._is_object_schema(definition.input_schema):
                errors.append("input_schema must be an object JSON Schema")

        if inspect.isabstract(tool):
            errors.append("tool contains unimplemented abstract methods")

        return ToolCheckResult(valid=not errors, errors=errors)

    @staticmethod
    def _is_object_schema(schema: Any) -> bool:
        return isinstance(schema, dict) and schema.get("type") == "object"
