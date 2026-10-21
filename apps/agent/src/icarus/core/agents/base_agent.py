"""Agent 能力抽象。"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator

from icarus.core.agents.types import AgentResponse
from icarus.core.events import Event
from icarus.core.run_control.types import AgentRunControl
from icarus.core.model_types import LLMRole
from icarus.core.models.messages import ImagePart, Message


class BaseAgent(ABC):
    """无状态 Agent 的统一同步和异步调用接口。"""

    @property
    @abstractmethod
    def model_role(self) -> LLMRole:
        ...

    @abstractmethod
    def invoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        ...

    @abstractmethod
    async def ainvoke(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AgentResponse:
        ...

    @abstractmethod
    def stream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> Iterator[Event]:
        ...

    @abstractmethod
    async def astream(
        self,
        system_prompt: str,
        history_messages: list[Message],
        input_prompt: str,
        input_images: list[ImagePart] | None = None,
        tools: list[str] | None = None,
        run_control: AgentRunControl | None = None,
    ) -> AsyncIterator[Event]:
        if False:
            yield
        ...
