"""No-tool Agent adapter for post-turn Skill maintenance planning."""

import asyncio
from collections.abc import Callable

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_models import (
    SkillMaintenancePlan,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_parser import (
    SkillMaintenanceParser,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_prompt import (
    SkillMaintenancePromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.model_provider.types import Message, TextPart


class SkillMaintainer:
    """Ask an isolated Agent for one validated maintenance plan."""

    def __init__(
        self,
        agent_provider: Callable[[], BaseAgent],
        prompt_builder: SkillMaintenancePromptBuilder,
        parser: SkillMaintenanceParser,
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.agent_provider = agent_provider
        self.prompt_builder = prompt_builder
        self.parser = parser
        self.timeout_seconds = timeout_seconds

    async def plan(
        self,
        *,
        messages: list[Message],
        tool_trace: tuple[object, ...],
        matched_skills: tuple[SkillDefinition, ...],
        session_skills: tuple[SkillDefinition, ...],
        skill_snapshots: tuple[object, ...],
    ) -> SkillMaintenancePlan:
        input_prompt = self.prompt_builder.build(
            messages=messages,
            tool_trace=tool_trace,
            matched_skills=matched_skills,
            session_skills=session_skills,
            skill_snapshots=skill_snapshots,
        )
        agent = self.agent_provider()
        response = await asyncio.wait_for(
            agent.ainvoke(
                system_prompt=self.prompt_builder.system_prompt,
                history_messages=[],
                input_prompt=input_prompt,
                input_images=None,
                tools=[],
            ),
            timeout=self.timeout_seconds,
        )
        return self.parser.parse(_response_text(response.message))


def _response_text(message: Message) -> str:
    texts: list[str] = []
    for part in message.content:
        if not isinstance(part, TextPart):
            raise ValueError(
                "Skill maintenance Agent response must contain only text"
            )
        texts.append(part.text)
    text = "".join(texts).strip()
    if not text:
        raise ValueError("Skill maintenance Agent returned empty text")
    return text
