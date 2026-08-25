import asyncio
import json

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.skill.evolver import (
    EVOLVER_SYSTEM_PROMPT,
    SkillEvolver,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_context import (
    get_generation_context,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_tools import (
    GENERATION_TOOL_NAMES,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillSnapshot,
)
from apps.agent.src.model_provider.types import Message, TextPart


class AgentStub:
    def __init__(self):
        self.calls = []
        self.context = None

    async def ainvoke(self, **kwargs):
        self.calls.append(kwargs)
        self.context = get_generation_context()
        return AgentResponse(
            message=Message("assistant", [TextPart("Evolution complete")]),
            finish_reason="stop",
            steps=2,
        )


def test_evolver_uses_draft_snapshot_context_and_tools(tmp_path):
    workspace = tmp_path / "workspace"
    draft = workspace / "skills" / ".drafts" / "draft"
    draft.mkdir(parents=True)
    global_skills = tmp_path / "data" / "skills"
    global_skills.mkdir(parents=True)
    snapshot = SkillSnapshot(
        name="existing",
        description="old",
        scope="global",
        path=global_skills / "existing" / "SKILL.md",
        directory_hash="abc123",
    )
    agent = AgentStub()
    evolver = SkillEvolver(lambda: agent, SkillGenerationPromptBuilder())

    result = asyncio.run(
        evolver.evolve(
            name="existing",
            instructions="improve it",
            conversation=[Message("user", [TextPart("evidence")])],
            snapshot=snapshot,
            draft_dir=draft,
            workspace_dir=workspace,
            global_skills_dir=global_skills,
            workspace_skills_dir=workspace / "skills",
        )
    )

    assert result is None
    call = agent.calls[0]
    assert call["system_prompt"] == EVOLVER_SYSTEM_PROMPT
    assert call["history_messages"] == []
    assert call["tools"] == GENERATION_TOOL_NAMES
    raw_payload = call["input_prompt"].split(
        "<skill_generation_data>\n", 1
    )[1].split("\n</skill_generation_data>", 1)[0]
    assert json.loads(raw_payload)["source_skill"]["directory_hash"] == "abc123"
    assert agent.context.draft_dir == draft.resolve()
