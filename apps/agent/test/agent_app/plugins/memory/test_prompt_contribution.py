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
        assert "without asking each time" in sections[0].content
        content = target.tool_scope_guidance()
        assert "sources or storage truthfully" in content
        assert "No hit" in content and "timeouts or failures" in content
        assert "credentials" in content
        assert "Confirm scope" in content
        assert "original user wording" in content and "service extracts" in content
        assert content.index("read its current text") < content.index("then correct")
        assert "set_recall" in content and "delete only" in content
        assert "empty store" in content
        assert "memory_get" not in content
        assert "memory_history" not in content
        from icarus.core.tools.result_budget import conservative_token_count
        assert conservative_token_count(sections[0].content) <= 400
        assert conservative_token_count(content) <= 1100
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
