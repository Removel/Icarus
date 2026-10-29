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
                "按行分页读取本地 UTF-8 文本，每次默认最多2000字符。"
                "先用相同行页的 next_text_offset 续读，truncated=false 后再用 next_offset 翻行页。"
                "预算裁剪时用原 offset/text_offset 减小 max_chars 重读，不能跳过隐藏正文；文件修改后重新读取。"
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
                    "text_offset": {"type": "integer", "minimum": 0, "default": 0},
                    "max_chars": {
                        "type": "integer", "minimum": 1, "maximum": 20000, "default": 2000,
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
        text_offset = arguments.get("text_offset", 0)
        if isinstance(text_offset, bool) or not isinstance(text_offset, int) or text_offset < 0:
            raise ValueError("text_offset must be a non-negative integer")
        max_chars = self._bounded_int(arguments.get("max_chars"), "max_chars", 2000, 20000)
        try:
            with path.open("r", encoding="utf-8") as file:
                rows = list(islice(file, offset - 1, offset - 1 + limit + 1))
        except (OSError, UnicodeError) as error:
            return ToolExecutionResult(success=False, error=str(error))
        has_more = len(rows) > limit
        visible = rows[:limit]
        text = "".join(visible)
        if text_offset > len(text):
            raise ValueError("text_offset exceeds the current line page length")
        end = min(len(text), text_offset + max_chars)
        truncated = end < len(text)
        return ToolExecutionResult(
            success=True,
            output={
                "path": str(path),
                "content": text[text_offset:end],
                "text_offset": text_offset,
                "max_chars": max_chars,
                "truncated": truncated,
                "next_text_offset": end if truncated else None,
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
