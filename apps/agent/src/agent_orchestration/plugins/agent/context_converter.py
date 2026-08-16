"""Blackboard Context 到 ReActAgent 参数的转换器。"""

from dataclasses import dataclass
import json

from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardContextReadyEvent,
    ContextBlock,
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


class BlackboardContextConverter:
    """稳定拍平动态插件上下文，并追加到当前 User Prompt。"""

    def convert(
        self,
        event: BlackboardContextReadyEvent,
    ) -> AgentInvocation:
        serialized_context = self.serialize_context(event.context_blocks)
        input_prompt = self.build_input_prompt(
            prompt=event.prompt,
            serialized_context=serialized_context,
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

    def serialize_context(self, context_blocks: list[ContextBlock]) -> str:
        normalized = [
            {
                "source_plugin_id": block.source_plugin_id,
                "context_type": block.context_type,
                "content": block.content,
                "metadata": dict(block.metadata),
            }
            for block in sorted(
                context_blocks,
                key=lambda block: (
                    block.source_plugin_id,
                    block.context_type,
                    block.content,
                    json.dumps(
                        block.metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )
        ]
        if not normalized:
            return ""
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def build_input_prompt(
        prompt: str,
        serialized_context: str,
        context_errors: dict[str, str],
    ) -> str:
        sections: list[str] = []
        if serialized_context:
            sections.append(
                "<plugin_context>\n"
                f"{serialized_context}\n"
                "</plugin_context>"
            )
        if context_errors:
            errors = json.dumps(
                context_errors,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            sections.append(
                "<plugin_context_errors>\n"
                f"{errors}\n"
                "</plugin_context_errors>"
            )
        sections.append(
            "<user_request>\n"
            f"{prompt}\n"
            "</user_request>"
        )
        return "\n\n".join(sections)
