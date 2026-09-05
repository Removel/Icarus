"""Agent 工具层统一类型。"""

from dataclasses import dataclass
from typing import Any

from apps.agent.src.model_provider.types import ImagePart


@dataclass(frozen=True)
class ToolExecutionResult:
    """一次本地工具执行的统一结果。"""

    success: bool
    output: Any | None = None
    error: str | None = None
    images: tuple[ImagePart, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = {
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }
        if self.images:
            value["images"] = [
                {
                    "source": image.source,
                    "source_type": image.source_type,
                    "media_type": image.media_type,
                }
                for image in self.images
            ]
        return value
