from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterator

from icarus.core.models.messages import (
    LLMResponse,
    LLMStreamChunk,
    Message,
    ToolDefinition,
)


class InvalidToolCallArgumentsError(ValueError):
    """模型返回的 tool call arguments 不是合法的 JSON 对象。"""

    def __init__(
        self,
        *,
        tool_name: str,
        raw_arguments: str,
        finish_reason: str | None,
        detail: str,
    ) -> None:
        self.tool_name = tool_name
        self.raw_arguments = raw_arguments
        self.finish_reason = finish_reason
        preview = raw_arguments[:200]
        if len(raw_arguments) > 200:
            preview += "…"
        super().__init__(
            "invalid tool call arguments: "
            f"tool={tool_name or '<unknown>'} "
            f"finish_reason={finish_reason} "
            f"detail={detail} "
            f"raw={preview!r}"
        )


class BaseLLM(ABC):
    """不同模型协议适配器需要实现的统一调用接口。"""

    @abstractmethod
    def invoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """同步调用并返回完整响应。"""

        ...

    @abstractmethod
    async def ainvoke(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """异步调用并返回完整响应。"""

        ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> Iterator[LLMStreamChunk]:
        """同步调用并逐个返回响应增量。"""

        ...

    @abstractmethod
    async def astream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """异步调用并逐个返回响应增量。"""

        if False:
            yield
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭同步客户端。"""

        ...

    @abstractmethod
    async def aclose(self) -> None:
        """关闭同步和异步客户端。"""

        ...


class LLMFactory:
    """Create a model by role without selecting a vendor or reading app settings."""

    def __init__(self, create: Callable[[str], BaseLLM]) -> None:
        self._create = create

    def create_llm(self, role: str) -> BaseLLM:
        return self._create(role)
