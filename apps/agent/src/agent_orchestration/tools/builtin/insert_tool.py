"""在本地文本文件的指定行前插入内容。"""

from pathlib import Path
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition
from apps.agent.src.model_provider.types import Message


class InsertTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="insert",
            description="在 UTF-8 文本文件的指定行前插入内容",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "从 1 开始的插入行号",
                    },
                    "content": {"type": "string", "description": "插入内容"},
                },
                "required": ["path", "line", "content"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        path = Path(self._required_string(arguments, "path"))
        line = arguments.get("line")
        content = self._required_string(arguments, "content", allow_empty=True)
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError("line must be a positive integer")

        try:
            original = path.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=True)
            if line > len(lines) + 1:
                return ToolExecutionResult(
                    success=False,
                    error=f"line out of range: {line}",
                )
            lines.insert(line - 1, content)
            path.write_text("".join(lines), encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))

        return ToolExecutionResult(
            success=True,
            output={"path": str(path), "line": line},
        )

    @staticmethod
    def _required_string(
        arguments: dict[str, Any],
        name: str,
        allow_empty: bool = False,
    ) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise ValueError(f"{name} must be a string")
        return value
