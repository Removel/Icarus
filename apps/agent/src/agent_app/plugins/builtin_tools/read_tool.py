"""读取本地文本文件。"""

from itertools import islice
from pathlib import Path
from typing import Any

from icarus.core.tools.base_tool import BaseTool
from icarus.core.tools.types import ToolExecutionResult
from icarus.core.models.messages import ToolDefinition
from icarus.core.models.messages import Message


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
                "按行分页读取本地 UTF-8 文本文件；用 offset/limit 选行页，"
                "再用 text_offset/max_chars 完成该页字符片段后翻页。"
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
                    "text_offset": {
                        "type": "integer", "minimum": 0, "default": 0,
                        "description": "当前行页内的字符偏移；保持 offset/limit 不变续读",
                    },
                    "max_chars": {
                        "type": "integer", "minimum": 1, "maximum": 20000,
                        "default": 2000,
                        "description": "片段字符上限；展示被裁剪时减小并重读相同偏移",
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
        unknown = set(arguments) - {"path", "offset", "limit", "text_offset", "max_chars"}
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
        offset = self._bounded_int(arguments.get("offset"), "offset", 1, 1_000_000_000)
        limit = self._bounded_int(
            arguments.get("limit"),
            "limit",
            self.default_limit,
            self.maximum_limit,
        )
        text_offset = self._bounded_int(
            arguments.get("text_offset"), "text_offset", 0, 1_000_000_000, minimum=0
        )
        max_chars = self._bounded_int(
            arguments.get("max_chars"), "max_chars", 2000, 20000
        )
        try:
            with path.open("r", encoding="utf-8") as file:
                rows = list(islice(file, offset - 1, offset - 1 + limit + 1))
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))
        has_more = len(rows) > limit
        page = "".join(rows[:limit])
        if text_offset > len(page):
            raise ValueError(f"text_offset out of range: {text_offset}")
        content = page[text_offset : text_offset + max_chars]
        next_text_offset = text_offset + len(content)
        truncated = next_text_offset < len(page)
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(path),
                "content": content,
                "text_offset": text_offset,
                "max_chars": max_chars,
                "truncated": truncated,
                "next_text_offset": next_text_offset if truncated else None,
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
    def _bounded_int(arguments, name, default, maximum, *, minimum=1):
        value = default if arguments is None else arguments
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
        return value
