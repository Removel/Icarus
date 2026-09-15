import asyncio

import pytest

from apps.agent.src.agent_orchestration.events import TaskErrorEvent
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin, PluginManager
from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardContextReadyEvent,
    BlackboardPlugin,
    BlackboardRegionUpdatedEvent,
    RegionDefinition,
    RegionInput,
    RegionOutput,
    RegionRegistry,
    RegionState,
    RegionStore,
)
from apps.agent.src.agent_orchestration.plugins.user_input import UserInputEvent


def memory_definition(**changes):
    values = {
        "region": "memory",
        "owner_plugin_id": "memory",
        "lifetime": "input",
        "required_for_start": True,
        "auto_expose": True,
        "allowed_statuses": ("idle", "recalling"),
        "initial_status": "idle",
        "max_summary_chars": 50,
        "max_refs": 3,
        "max_data_chars": 200,
    }
    values.update(changes)
    return RegionDefinition(**values)


def test_region_registry注册冻结和释放():
    registry = RegionRegistry()
    registration = registry.register(memory_definition())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(memory_definition())

    registration.release()
    registration.release()
    assert registry.definitions() == ()

    registry.register(memory_definition())
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(memory_definition(region="other"))


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"lifetime": "session"}, "Only input Regions"),
        ({"allowed_statuses": ("idle",)}, "initial_status"),
        ({"max_refs": 0}, "max_refs"),
    ],
)
def test_region_definition拒绝非法契约(changes, message):
    if changes.get("lifetime") == "session":
        changes["required_for_start"] = True
    if changes.get("allowed_statuses") == ("idle",):
        changes["initial_status"] = "recalling"
    with pytest.raises(ValueError, match=message):
        memory_definition(**changes)


def test_region_store只接受owner和当前input并执行预算校验():
    registry = RegionRegistry()
    registry.register(memory_definition())
    registry.freeze()
    store = RegionStore(registry)
    required = store.begin_input("input-1")
    assert required == frozenset({"memory"})

    with pytest.raises(PermissionError, match="owned"):
        store.apply(
            source_plugin_id="skill",
            task_id="task-1",
            current_task_id="task-1",
            region="memory",
            input_id="input-1",
            input_value=None,
            output=None,
            state=RegionState("idle"),
            complete_for_input=True,
        )

    with pytest.raises(ValueError, match="current input_id"):
        store.apply(
            source_plugin_id="memory",
            task_id="task-1",
            current_task_id="task-1",
            region="memory",
            input_id="old-input",
            input_value=None,
            output=None,
            state=RegionState("idle"),
            complete_for_input=True,
        )

    with pytest.raises(ValueError, match="summary"):
        store.apply(
            source_plugin_id="memory",
            task_id="task-1",
            current_task_id="task-1",
            region="memory",
            input_id="input-1",
            input_value=None,
            output=RegionOutput(summary="x" * 51),
            state=RegionState("idle"),
            complete_for_input=True,
        )

    snapshot = store.apply(
        source_plugin_id="memory",
        task_id="task-1",
        current_task_id="task-1",
        region="memory",
        input_id="input-1",
        input_value=RegionInput(summary="recall"),
        output=RegionOutput(
            summary="one hit",
            refs=("memory:1",),
            data={"items": [{"ref": "memory:1", "content": "value"}]},
        ),
        state=RegionState("idle"),
        complete_for_input=True,
    )
    assert snapshot.owner_plugin_id == "memory"
    assert store.compact_view()["memory"]["summary"] == "one hit"


def test_region_data是深层不可变快照且导出为普通json():
    source = {"items": [{"ref": "memory:1", "tags": ["a"]}]}
    output = RegionOutput(data=source)
    source["items"][0]["tags"].append("changed")

    assert output.data["items"][0]["tags"] == ("a",)
    with pytest.raises(TypeError):
        output.data["items"][0]["ref"] = "other"


def test_blackboard_required_region完成前不启动并在完成后只启动一次():
    async def run():
        registry = RegionRegistry()
        registry.register(memory_definition())
        plugin = BlackboardPlugin(
            "blackboard",
            required_context_sources=set(),
            region_registry=registry,
            region_store=RegionStore(registry),
        )
        events = []
        plugin.bind_publisher(lambda event: _append(events, event))
        await plugin.start()
        user_input = UserInputEvent(task_id="task-1", prompt="hello")
        await plugin.consume("user-input", user_input)
        assert events == []
        update = BlackboardRegionUpdatedEvent(
            task_id="task-1",
            region="memory",
            input_id=user_input.event_id,
            input=RegionInput(summary="recall"),
            output=RegionOutput(summary="empty"),
            state=RegionState("idle"),
            complete_for_input=True,
        )
        await plugin.consume("memory", update)
        await plugin.consume("memory", update)
        return plugin, events

    plugin, events = asyncio.run(run())
    contexts = [item for item in events if isinstance(item, BlackboardContextReadyEvent)]
    assert len(contexts) == 1
    assert '<blackboard_regions>\n{"memory":' in contexts[0].input_prompt
    assert plugin.get_task_state("task-1").completed_regions == {"memory"}


def test_blackboard_region事件早于user_input时有界缓存并在输入到达后处理():
    async def run():
        registry = RegionRegistry()
        registry.register(memory_definition())
        plugin = BlackboardPlugin(
            "blackboard", set(),
            region_registry=registry, region_store=RegionStore(registry),
        )
        events = []
        plugin.bind_publisher(lambda event: _append(events, event))
        await plugin.start()
        user_input = UserInputEvent(task_id="task-1", prompt="hello")
        await plugin.consume(
            "memory",
            BlackboardRegionUpdatedEvent(
                task_id="task-1", region="memory",
                input_id=user_input.event_id, input=None,
                output=RegionOutput(summary="ready"),
                state=RegionState("idle"), complete_for_input=True,
            ),
        )
        assert events == []
        with pytest.raises(KeyError, match="not found"):
            plugin.get_task_state("task-1")
        await plugin.consume("user-input", user_input)
        return events

    events = asyncio.run(run())
    assert len(events) == 1
    assert isinstance(events[0], BlackboardContextReadyEvent)


def test_blackboard_preinput_region缓存有界且拒绝非owner():
    async def run():
        registry = RegionRegistry()
        registry.register(memory_definition())
        plugin = BlackboardPlugin(
            "blackboard", set(),
            region_registry=registry, region_store=RegionStore(registry),
        )
        events = []
        plugin.bind_publisher(lambda event: _append(events, event))
        await plugin.start()
        sample = BlackboardRegionUpdatedEvent(
            task_id="bad", region="memory", input_id="input",
            input=None, output=None, state=RegionState("idle"),
            complete_for_input=False,
        )
        await plugin.consume("skill", sample)
        for index in range(140):
            await plugin.consume(
                "memory",
                BlackboardRegionUpdatedEvent(
                    task_id=f"task-{index}", region="memory",
                    input_id=f"input-{index}", input=None, output=None,
                    state=RegionState("recalling"), complete_for_input=False,
                ),
            )
        return plugin, events

    plugin, events = asyncio.run(run())
    assert events[0].code == "region_update_rejected"
    assert len(plugin._pending_region_updates) == 128


def test_blackboard非法region更新变成非致命错误且不放行():
    async def run():
        registry = RegionRegistry()
        registry.register(memory_definition())
        plugin = BlackboardPlugin(
            "blackboard", set(),
            region_registry=registry, region_store=RegionStore(registry),
        )
        events = []
        plugin.bind_publisher(lambda event: _append(events, event))
        await plugin.start()
        user_input = UserInputEvent(task_id="task-1", prompt="hello")
        await plugin.consume("user-input", user_input)
        await plugin.consume(
            "skill",
            BlackboardRegionUpdatedEvent(
                task_id="task-1", region="memory",
                input_id=user_input.event_id, input=None, output=None,
                state=RegionState("idle"), complete_for_input=True,
            ),
        )
        return events

    events = asyncio.run(run())
    assert len(events) == 1
    assert isinstance(events[0], TaskErrorEvent)
    assert events[0].fatal is False
    assert events[0].code == "region_update_rejected"


async def _append(values, value):
    values.append(value)


class _Producer(BasePlugin):
    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id, event


class _ContextSink(BasePlugin):
    def __init__(self, plugin_id: str) -> None:
        super().__init__(plugin_id)
        self.contexts = []

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if isinstance(event, BlackboardContextReadyEvent):
            self.contexts.append((source_plugin_id, event))


def test_region_e2e经过eventbus阻塞再放行agent():
    async def run():
        registry = RegionRegistry()
        registry.register(memory_definition())
        store = RegionStore(registry)
        manager = PluginManager()
        user_input = _Producer("user-input")
        memory = _Producer("memory")
        blackboard = BlackboardPlugin(
            "blackboard", set(),
            region_registry=registry, region_store=store,
        )
        agent = _ContextSink("agent")
        for plugin in (user_input, memory, blackboard, agent):
            manager.register(plugin)
        manager.subscribe("blackboard", "user-input")
        manager.subscribe("blackboard", "memory")
        manager.subscribe("agent", "blackboard")
        await manager.start()
        event = UserInputEvent(task_id="task-e2e", prompt="remember")
        await user_input.publish(event)
        await manager.event_bus.drain()
        await manager.drain_plugin("blackboard")
        assert agent.contexts == []
        await memory.publish(
            BlackboardRegionUpdatedEvent(
                task_id="task-e2e", region="memory",
                input_id=event.event_id, input=None,
                output=RegionOutput(summary="ready", refs=("memory:1",)),
                state=RegionState("idle"), complete_for_input=True,
            )
        )
        await manager.stop(timeout=1)
        return agent.contexts

    contexts = asyncio.run(run())
    assert len(contexts) == 1
    source, event = contexts[0]
    assert source == "blackboard"
    assert "ready" in event.input_prompt
