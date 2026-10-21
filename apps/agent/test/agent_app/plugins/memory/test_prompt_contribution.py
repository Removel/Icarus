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
        assert "你自己的长期记忆" in content
        assert "我记不起来了" in content
        assert "凭据" in content
        assert "涉及多条记忆的停止或删除，先确认范围" in content
        assert "无需每次征求用户确认" in content
        assert "原始片段" in content
        assert "不改写、不总结、不翻译" in content
        assert "项目级" in content and "全局级" in content
        assert "memory_read" in content
        assert "include_history" in content
        assert "memory_set_recall" in content
        assert "记忆库为空" in content
        assert "memory_get" not in content
        assert "memory_history" not in content
        assert len(content) <= 700
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
