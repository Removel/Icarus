"""定义屏蔽不同模型协议差异的统一输入输出类型。"""

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


class ImageAssetUnavailableError(ValueError):
    """Session Asset 无法解析为可读取图片。"""


@dataclass(frozen=True)
class TextPart:
    """消息中的文本内容。"""

    text: str


@dataclass(frozen=True, init=False)
class ImagePart:
    """消息中的远程图片或 Session Asset 引用。"""

    source: str
    source_type: Literal["url", "asset"] = "url"
    media_type: str | None = None

    def __init__(
        self,
        source: str | None = None,
        source_type: Literal["url", "asset"] | str = "url",
        media_type: str | None = None,
        *,
        url: str | None = None,
    ) -> None:
        if source is None:
            source = url
        elif url is not None:
            raise ValueError("image source and url cannot both be provided")
        # 兼容旧的 ImagePart(url, media_type) 二位置参数。
        if (
            isinstance(source_type, str)
            and source_type.startswith("image/")
            and media_type is None
        ):
            media_type = source_type
            source_type = "url"
        if not source:
            raise ValueError("image source cannot be empty")
        if source_type not in {"url", "asset"}:
            raise ValueError("image source_type must be url or asset")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "media_type", media_type)

    @property
    def url(self) -> str:
        """兼容只读取旧 URL 字段的调用方。"""

        return self.source


ContentPart: TypeAlias = TextPart | ImagePart
MessageRole: TypeAlias = Literal["system", "user", "assistant", "tool"]
FinishReason: TypeAlias = Literal[
    "stop",
    "length",
    "tool_call",
    "content_filter",
    "error",
    "other",
]


@dataclass(frozen=True)
class ToolDefinition:
    """提供给模型的工具定义，input_schema 使用 JSON Schema。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """模型生成的完整工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """一条统一格式的多模态对话消息。"""

    role: MessageRole
    content: list[ContentPart]
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    """一次模型调用的 token 用量。"""

    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMResponse:
    """非流式调用返回的完整响应。"""

    message: Message
    reasoning: str | None = None
    usage: Usage | None = None
    finish_reason: FinishReason | None = None


@dataclass(frozen=True)
class LLMStreamChunk:
    """流式调用的增量文本或完整工具调用。"""

    text_delta: str = ""
    reasoning_delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: FinishReason | None = None
