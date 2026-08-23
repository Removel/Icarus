"""写入本地文本文件。"""

from pathlib import Path
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition
from apps.agent.src.model_provider.types import Message


class WriteTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write",
            description="使用 UTF-8 覆盖写入本地文本文件",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "写入内容"},
                },
                "required": ["path", "content"],
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
        content = self._required_string(arguments, "content", allow_empty=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(path),
                "bytes_written": len(content.encode("utf-8")),
            },
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
