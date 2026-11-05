import asyncio

from agent_app.contracts.prompt import PromptContributionEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.run_control import TaskContextInputEvent
from apps.agent.test.agent_app.plugins.memory.test_plugin import BackendStub, bind_background, item, plugin


def test_memory_guidance_is_static_session_and_recall_stays_dynamic():
    async def run():
        target = plugin(BackendStub([item(content="UNIQUE-MEMORY-FACT")]))
        sections = target.static_prompt_sections()
        assert len(sections) == 1
        assert sections[0].layer == "session"
        assert sections[0].section_id == "memory-use"
        content = sections[0].content
        assert "来源或存储" in content
        assert "无命中" in content and "超时或故障" in content
        assert "凭据" in content
        assert "涉及多条先确认范围" in content
        assert "无需每次征求确认" in content
        assert "原话" in content and "服务抽取" in content
        assert content.index("memory_read") < content.index("memory_correct")
        assert "memory_set_recall" in content and "memory_delete" in content
        assert "记忆库为空" in content
        assert "memory_get" not in content
        assert "memory_history" not in content
        from icarus.core.tools.result_budget import conservative_token_count
        assert conservative_token_count(content) <= 450
        assert "UNIQUE-MEMORY-FACT" not in content

        published = []
        bind_background(target, published)
        await target.consume("user-input", UserInputEvent(task_id="task", prompt="hello"))
        await asyncio.gather(*target._recall_tasks)
        assert not any(isinstance(event, PromptContributionEvent) for event in published)
        recalled = [event for event in published if isinstance(event, TaskContextInputEvent)]
        assert len(recalled) == 1 and "UNIQUE-MEMORY-FACT" in recalled[0].content
        await target.stop()
    asyncio.run(run())
