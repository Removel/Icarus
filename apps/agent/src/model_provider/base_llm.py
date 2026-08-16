from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator

from apps.agent.src.model_provider.types import (
    LLMResponse,
    LLMStreamChunk,
    Message,
    ToolDefinition,
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

        ...

    @abstractmethod
    def close(self) -> None:
        """关闭同步客户端。"""

        ...

    @abstractmethod
    async def aclose(self) -> None:
        """关闭同步和异步客户端。"""

        ...
