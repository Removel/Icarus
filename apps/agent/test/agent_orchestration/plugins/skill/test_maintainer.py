import asyncio
import json

import pytest

from apps.agent.src.agent_orchestration.capability import AgentResponse
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_parser import (
    SkillMaintenanceParser,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_prompt import (
    SkillMaintenancePromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintainer import (
    SkillMaintainer,
)
from apps.agent.src.model_provider.types import Message, TextPart


class AgentStub:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def ainvoke(self, **kwargs):
        self.calls.append(kwargs)
        return AgentResponse(
            message=Message("assistant", [TextPart(self.text)]),
            finish_reason="stop",
            steps=1,
        )


def no_op_text():
    return json.dumps(
        {
            "operations": [
                {
                    "action": "no_op",
                    "reason": "nothing reusable",
                }
            ]
        }
    )


def test_maintainer使用独立system_prompt且不给工具或history():
    async def run():
        agent = AgentStub(no_op_text())
        maintainer = SkillMaintainer(
            lambda: agent,
            SkillMaintenancePromptBuilder(),
            SkillMaintenanceParser(),
        )
        plan = await maintainer.plan(
            messages=[Message("user", [TextPart("turn")])],
            tool_trace=(),
            matched_skills=(),
            session_skills=(),
            skill_snapshots=(),
        )
        return agent, plan

    agent, plan = asyncio.run(run())

    assert plan.operations[0].action == "no_op"
    call = agent.calls[0]
    assert call["system_prompt"] == SkillMaintenancePromptBuilder.system_prompt
    assert call["history_messages"] == []
    assert call["tools"] == []
    assert "<skill_maintenance_data>" in call["input_prompt"]


def test_maintainer非法输出直接失败不返回计划():
    async def run():
        maintainer = SkillMaintainer(
            lambda: AgentStub("not json"),
            SkillMaintenancePromptBuilder(),
            SkillMaintenanceParser(),
        )
        await maintainer.plan(
            messages=[],
            tool_trace=(),
            matched_skills=(),
            session_skills=(),
            skill_snapshots=(),
        )

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_maintainer超时向上抛出():
    class SlowAgent:
        async def ainvoke(self, **kwargs):
            await asyncio.Event().wait()

    async def run():
        maintainer = SkillMaintainer(
            SlowAgent,
            SkillMaintenancePromptBuilder(),
            SkillMaintenanceParser(),
            timeout_seconds=0.01,
        )
        await maintainer.plan(
            messages=[],
            tool_trace=(),
            matched_skills=(),
            session_skills=(),
            skill_snapshots=(),
        )

    with pytest.raises(TimeoutError):
        asyncio.run(run())


def test_maintainer只在实际plan调用时创建agent():
    created = []

    def provider():
        created.append(True)
        return AgentStub(no_op_text())

    maintainer = SkillMaintainer(
        provider,
        SkillMaintenancePromptBuilder(),
        SkillMaintenanceParser(),
    )

    assert created == []
    asyncio.run(
        maintainer.plan(
            messages=[],
            tool_trace=(),
            matched_skills=(),
            session_skills=(),
            skill_snapshots=(),
        )
    )
    assert created == [True]
