import asyncio
import hashlib
import json

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.skill.evolver import (
    EVOLVER_SYSTEM_PROMPT,
    SkillEvolver,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_parser import (
    SkillGenerationParser,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import SkillSnapshot
from apps.agent.src.model_provider.types import Message, TextPart


class AgentStub:
    def __init__(self, content):
        self.calls = []
        self.content = content

    async def ainvoke(self, **kwargs):
        self.calls.append(kwargs)
        return AgentResponse(
            message=Message("assistant", [TextPart(json.dumps({"content": self.content}))]),
            finish_reason="stop",
            steps=1,
        )


def test_evolver_uses_explicit_snapshot_empty_history_and_no_tools(tmp_path):
    old = "---\nname: existing\ndescription: old\n---\nold"
    new = "---\nname: existing\ndescription: new\n---\nnew"
    snapshot = SkillSnapshot(
        name="existing",
        description="old",
        scope="global",
        path=tmp_path / "existing" / "SKILL.md",
        content=old,
        content_hash=hashlib.sha256(old.encode()).hexdigest(),
    )
    agent = AgentStub(new)
    evolver = SkillEvolver(
        lambda: agent,
        SkillGenerationPromptBuilder(),
        SkillGenerationParser(),
    )

    result = asyncio.run(
        evolver.evolve(
            name="existing",
            instructions="improve it",
            conversation=[Message("user", [TextPart("evidence")])],
            snapshot=snapshot,
        )
    )

    assert result == new
    call = agent.calls[0]
    assert call["system_prompt"] == EVOLVER_SYSTEM_PROMPT
    assert call["history_messages"] == []
    assert call["tools"] == []
    raw_payload = call["input_prompt"].split(
        "<skill_generation_data>\n", 1
    )[1].split("\n</skill_generation_data>", 1)[0]
    assert json.loads(raw_payload)["source_skill"]["content"] == old
