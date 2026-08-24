import asyncio
import json

import pytest

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.skill.generation_parser import (
    SkillGenerationParser,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
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

    async def ainvoke(self, **kwargs):
        self.calls.append(kwargs)
        return AgentResponse(
            message=self.message,
            finish_reason="stop",
            steps=1,
        )


def producer(agent, *, timeout=1):
    return SkillProducer(
        lambda: agent,
        SkillGenerationPromptBuilder(),
        SkillGenerationParser(),
        timeout_seconds=timeout,
    )


def test_producer_uses_empty_history_and_no_tools():
    content = "---\nname: new\ndescription: test\n---\nbody"
    agent = AgentStub(
        Message("assistant", [TextPart(json.dumps({"content": content}))])
    )

    result = asyncio.run(
        producer(agent).produce(
            name="new",
            scope="workspace",
            instructions="create it",
            conversation=[Message("user", [TextPart("context")])],
        )
    )

    assert result == content
    call = agent.calls[0]
    assert call["system_prompt"] == PRODUCER_SYSTEM_PROMPT
    assert call["history_messages"] == []
    assert call["tools"] == []
    assert "context" in call["input_prompt"]


def test_producer_rejects_non_text_response():
    agent = AgentStub(Message("assistant", [ImagePart("https://x/a.png")]))
    with pytest.raises(ValueError, match="only text"):
        asyncio.run(
            producer(agent).produce(
                name="new",
                scope="workspace",
                instructions="create",
                conversation=[],
            )
        )


def test_producer_timeout_propagates():
    class SlowAgent:
        async def ainvoke(self, **kwargs):
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        asyncio.run(
            producer(SlowAgent(), timeout=0.01).produce(
                name="new",
                scope="workspace",
                instructions="create",
                conversation=[],
            )
        )
