import asyncio

import pytest

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.skill.generation_context import (
    get_generation_context,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_tools import (
    GENERATION_TOOL_NAMES,
)
from apps.agent.src.agent_orchestration.plugins.skill.producer import (
    PRODUCER_SYSTEM_PROMPT,
    SkillProducer,
)
from apps.agent.src.model_provider.types import ImagePart, Message, TextPart


class AgentStub:
    def __init__(self, message):
        self.message = message
        self.calls = []
        self.context = None

    async def ainvoke(self, **kwargs):
        self.calls.append(kwargs)
        self.context = get_generation_context()
        return AgentResponse(message=self.message, finish_reason="stop", steps=2)


def paths(tmp_path):
    workspace = tmp_path / "workspace"
    draft = workspace / "skills" / ".drafts" / "draft"
    draft.mkdir(parents=True)
    global_skills = tmp_path / "data" / "skills"
    global_skills.mkdir(parents=True)
    return {
        "draft_dir": draft,
        "workspace_dir": workspace,
        "global_skills_dir": global_skills,
        "workspace_skills_dir": workspace / "skills",
    }


def test_producer_uses_full_context_and_generation_tools(tmp_path):
    agent = AgentStub(Message("assistant", [TextPart("Draft complete")]))
    producer = SkillProducer(lambda: agent, SkillGenerationPromptBuilder())

    result = asyncio.run(
        producer.produce(
            name="new",
            scope="workspace",
            instructions="create it",
            conversation=[Message("user", [TextPart("context")])],
            **paths(tmp_path),
        )
    )

    assert result is None
    call = agent.calls[0]
    assert call["system_prompt"] == PRODUCER_SYSTEM_PROMPT
    assert call["history_messages"] == []
    assert call["tools"] == GENERATION_TOOL_NAMES
    assert "context" in call["input_prompt"]
    assert agent.context.draft_dir.name == "draft"
    with pytest.raises(RuntimeError, match="not active"):
        get_generation_context()


def test_producer_rejects_non_text_response(tmp_path):
    agent = AgentStub(Message("assistant", [ImagePart("https://x/a.png")]))
    with pytest.raises(ValueError, match="only text"):
        asyncio.run(
            SkillProducer(lambda: agent, SkillGenerationPromptBuilder()).produce(
                name="new",
                scope="workspace",
                instructions="create",
                conversation=[],
                **paths(tmp_path),
            )
        )


def test_producer_has_no_total_timeout_and_propagates_cancellation(tmp_path):
    class SlowAgent:
        async def ainvoke(self, **kwargs):
            del kwargs
            await asyncio.Event().wait()

    async def run():
        task = asyncio.create_task(
            SkillProducer(lambda: SlowAgent(), SkillGenerationPromptBuilder()).produce(
                name="new",
                scope="workspace",
                instructions="create",
                conversation=[],
                **paths(tmp_path),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
