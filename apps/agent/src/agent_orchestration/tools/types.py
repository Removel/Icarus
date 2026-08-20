"""Agent 工具层统一类型。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolExecutionResult:
    """一次本地工具执行的统一结果。"""

    success: bool
    output: Any | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }
