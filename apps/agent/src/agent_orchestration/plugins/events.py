"""具体编排 Plugin 使用的 Event。"""

from dataclasses import dataclass, field

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.types import ImagePart, Message


@dataclass(frozen=True, kw_only=True)
class AgentContextReadyEvent(Event):
    """Blackboard 向 AgentPlugin 提供的一次完整 Agent 输入。"""

    model_role: LLMRole
    system_prompt: str
    history_messages: list[Message] = field(default_factory=list)
    input_prompt: str
    input_images: list[ImagePart] = field(default_factory=list)
    tools: list[str] | None = None
