"""读取本地文本文件。"""

from itertools import islice
from pathlib import Path
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition
from apps.agent.src.model_provider.types import Message


class ReadTool(BaseTool):
    def __init__(
        self, *, default_limit: int = 200, maximum_limit: int = 2000
    ) -> None:
        if not 1 <= default_limit <= maximum_limit:
            raise ValueError(
                "default_limit must be between 1 and maximum_limit"
            )
        self.default_limit = default_limit
        self.maximum_limit = maximum_limit

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read",
            description=(
                "按行分页读取本地 UTF-8 文本文件；使用 offset/limit 继续读取。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "需要读取的文件路径",
                    },
                    "offset": {
                        "type": "integer", "minimum": 1, "default": 1
                    },
                    "limit": {
                        "type": "integer", "minimum": 1,
                        "maximum": self.maximum_limit,
                        "default": self.default_limit,
                    },
                },
                "required": ["path"],
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
        unknown = set(arguments) - {"path", "offset", "limit"}
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
        offset = self._bounded_int(arguments.get("offset"), "offset", 1, 1_000_000_000)
        limit = self._bounded_int(
            arguments.get("limit"),
            "limit",
            self.default_limit,
            self.maximum_limit,
        )
        try:
            with path.open("r", encoding="utf-8") as file:
                rows = list(islice(file, offset - 1, offset - 1 + limit + 1))
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))
        has_more = len(rows) > limit
        visible = rows[:limit]
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(path),
                "content": "".join(visible),
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
            },
        )

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True

    @staticmethod
    def _required_string(arguments: dict[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _bounded_int(arguments, name, default, maximum):
        value = default if arguments is None else arguments
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        return value
