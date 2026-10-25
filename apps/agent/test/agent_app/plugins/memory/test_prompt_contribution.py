import asyncio

import pytest

from agent_app.plugins.blackboard import BlackboardPlugin, RegionRegistry, RegionStore
from agent_app.plugins.blackboard.tools import create_blackboard_tools
from agent_app.plugins.memory.factory import create_plugin
from agent_app.plugins.persistence import SessionIdentity
from icarus.core.contracts.context import BlackboardContextReadyEvent, BlackboardRegionUpdatedEvent
from icarus.core.run_control import TaskContextInputResultEvent

from agent_app.contracts.prompt import PromptContributionEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.run_control import TaskContextInputEvent
from apps.agent.test.agent_app.plugins.memory.test_plugin import BackendStub, bind_background, item, plugin


@pytest.mark.parametrize("outcome", ["empty", "failure", "timeout", "nonempty"])
def test_memory真实blackboard组合隐藏自动状态但保留上下文和显式诊断(tmp_path, monkeypatch, outcome):
    backend = BackendStub(
        [item(content="UNIQUE-MEMORY-FACT")] if outcome == "nonempty" else (),
        delay=1 if outcome == "timeout" else 0,
        error=RuntimeError("unavailable") if outcome == "failure" else None,
    )
    monkeypatch.setattr("agent_app.plugins.memory.factory.Mem0HttpAdapter", lambda *args, **kwargs: backend)
    target = create_plugin("memory", tmp_path, "session", {
        "identity": SessionIdentity.create(tmp_path, "session"),
        "user_id": "u", "agent_id": "a", "recall": {"deadline_ms": 100},
    }, {}, None).plugin

    async def run():
        registry = RegionRegistry()
        target.region_registration = registry.register(target.region_definition)
        store = RegionStore(registry)
        blackboard = BlackboardPlugin("blackboard", set(), region_registry=registry, region_store=store)
        contexts, dynamic, pending = [], [], []

        async def publish_context(event):
            if isinstance(event, BlackboardContextReadyEvent):
                contexts.append(event)

        async def publish_memory(event):
            if isinstance(event, BlackboardRegionUpdatedEvent):
                await blackboard.consume("memory", event)
            elif isinstance(event, TaskContextInputEvent):
                dynamic.append(event.content)
                await target.consume("agent", TaskContextInputResultEvent(
                    task_id=event.task_id, request_event_id=event.event_id, status="accepted",
                ))

        def start_background(name, operation):
            task = asyncio.create_task(operation(), name=name)
            pending.append(task)
            return task

        blackboard.bind_publisher(publish_context)
        target.bind_publisher(publish_memory)
        target.bind_background_work_starter(start_background)
        await blackboard.start()
        event = UserInputEvent(task_id="task", prompt="hello")
        await blackboard.consume("user-input", event)
        assert contexts == []  # required_for_start still gates Agent startup.
        await target.consume("user-input", event)
        await asyncio.wait_for(asyncio.gather(*pending), timeout=0.5)
        assert len(contexts) == 1
        composed = contexts[0].input_prompt
        assert "我记不起来了" not in composed
        assert '"refs":[]' not in composed
        assert '"idle"' not in composed
        assert "<blackboard_regions>" not in composed
        assert blackboard.get_task_state("task").completed_regions == {"memory"}
        if outcome == "nonempty":
            assert len(dynamic) == 1
            assert '"content":"UNIQUE-MEMORY-FACT"' in dynamic[0]
        else:
            assert dynamic == []

        tools = {tool.definition.name: tool for tool in create_blackboard_tools(store)}
        listed = tools["blackboard_list"].invoke({})
        assert listed.success is True
        assert listed.output["regions"][0]["region"] == "memory"
        assert listed.output["regions"][0]["status"] == "idle"
        read = tools["blackboard_read"].invoke({"region": "memory"})
        assert read.success is True
        output = read.output["sections"]["output"]
        expected_error = {"empty": None, "failure": "backend_unavailable", "timeout": "timeout", "nonempty": None}[outcome]
        assert output["error"] == expected_error
        if outcome == "empty":
            assert output["summary"] == "我记不起来了"
            recall = next(tool for tool in target.build_tools() if tool.definition.name == "memory_recall")
            explicit = recall.invoke({"query": "hello"})
            assert explicit.success is True
            assert explicit.output["items"] == []
            assert explicit.output["query"] == "hello"
        await target.stop()
        await blackboard.stop()

    asyncio.run(run())


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
