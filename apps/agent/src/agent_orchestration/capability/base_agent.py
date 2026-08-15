"""Agent 能力抽象。"""

from abc import ABC, abstractmethod

from apps.agent.src.agent_orchestration.capability.types import AgentResponse
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.types import ImagePart, Message


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
    ) -> AgentResponse:
        ...
