"""Blackboard Context 到 ReActAgent 参数的转换器。"""

from dataclasses import dataclass

from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardContextReadyEvent,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.prompt_composer import (
    BlackboardPromptComposer,
)
from apps.agent.src.model_config import LLMRole
from apps.agent.src.model_provider.types import ImagePart, Message


@dataclass(frozen=True)
class AgentInvocation:
    model_role: LLMRole
    system_prompt: str
    history_messages: list[Message]
    input_prompt: str
    input_images: list[ImagePart]
    tools: list[str] | None


class BlackboardContextConverter(BlackboardPromptComposer):
    """稳定拍平动态插件上下文，并追加到当前 User Prompt。"""

    def convert(
        self,
        event: BlackboardContextReadyEvent,
    ) -> AgentInvocation:
        input_prompt = event.input_prompt
        if input_prompt is None:
            input_prompt = self.compose(
                prompt=event.prompt,
                context_blocks=event.context_blocks,
                context_errors=event.context_errors,
            )
        return AgentInvocation(
            model_role=event.model_role,
            system_prompt=event.system_prompt,
            history_messages=list(event.history_messages),
            input_prompt=input_prompt,
            input_images=list(event.input_images),
            tools=None if event.tools is None else list(event.tools),
        )
