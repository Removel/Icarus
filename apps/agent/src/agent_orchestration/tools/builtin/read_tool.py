"""读取本地文本文件。"""

from pathlib import Path
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class ReadTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description="读取本地 UTF-8 文本文件",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "需要读取的文件路径",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        path = Path(self._required_string(arguments, "path"))
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))
        return ToolExecutionResult(
            success=True,
            output={"path": str(path), "content": content},
        )

    @staticmethod
    def _required_string(arguments: dict[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value
