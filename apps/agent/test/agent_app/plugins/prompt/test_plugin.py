import asyncio
from datetime import UTC, datetime
import importlib
import logging

import pytest

from agent_app.contracts.prompt import PromptContributionEvent, PromptReadRequest, PromptSection, PromptUpdateEvent
from icarus.core.contracts.context import ContextContributionEvent
from icarus.core.contracts.input import InputFinishedEvent, UserInputEvent
from icarus.core.plugin import BasePlugin, handles
from icarus.core.run_control import TaskChannelRegistry, TaskContextInputEvent, TaskContextInputResultEvent
from icarus.runtime.manager import PluginManager


class Consumer(BasePlugin):
    def __init__(self):
        super().__init__("consumer")
        self.results = asyncio.Queue()
        self.inputs = asyncio.Queue()

    @handles(ContextContributionEvent)
    async def on_context(self, source, event):
        await self.results.put(event)

    @handles(TaskContextInputEvent)
    async def on_input(self, source, event):
        await self.inputs.put(event)
        channel = self.channels.get(event.task_id)
        await self.publish(TaskContextInputResultEvent(task_id=event.task_id, request_event_id=event.event_id, status="accepted", run_id=channel.run_id if channel else None))


async def setup(root, *, required=(), timeout=0.1, sources=("producer",)):
    try:
        cls = importlib.import_module("agent_app.plugins.prompt.plugin").PromptPlugin
    except ModuleNotFoundError:
        pytest.fail("Prompt plugin is not implemented")
    from agent_app.plugins.prompt.config import PluginConfig
    channels = TaskChannelRegistry()
    channels.create("task")
    plugin = cls("prompt", workspace_path=root, config=PluginConfig(contributor_timeout_seconds=timeout, required_contributors=list(required)), instructions="EXTERNAL", task_channels=channels, logger=logging.getLogger("test"), base_prompt="BASE")
    plugin.context_result_source_id = "consumer"
    plugin.prepare({}, dynamic_sources=frozenset(sources), update_sources=frozenset(sources), required_sources=frozenset(required), measure_dynamic=lambda system, content: len(system) + len(content) + 50)
    manager = PluginManager()
    consumer = Consumer()
    consumer.channels = channels
    producer = BasePlugin("producer")
    manager.register(plugin)
    manager.register(consumer)
    manager.register(producer)
    manager.subscribe("consumer", "prompt")
    manager.subscribe("prompt", "producer")
    manager.subscribe("prompt", "consumer")
    manager.bind_capability("prompt.read", "prompt", required_by={"consumer"})
    await manager.start()
    return manager, plugin, producer, consumer, channels


def test_prompt_aggregates_events_without_blocking_static_capability(tmp_path):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            response = await asyncio.wait_for(consumer.request(PromptReadRequest()), 0.5)
            assert "BASE" in response.unwrap().system_prompt
            assert "EXTERNAL" in response.system_prompt
            await producer.publish(PromptContributionEvent(task_id="task", status="completed", sections=(PromptSection("mode", "mode", "DYNAMIC"),)))
            result = await asyncio.wait_for(consumer.results.get(), 1)
            assert result.status == "completed"
            assert len(result.context_blocks) == 1
            assert result.context_blocks[0].source_plugin_id == "prompt"
            assert "DYNAMIC" in result.context_blocks[0].content
            await producer.publish(PromptContributionEvent(task_id="task", status="completed", sections=(PromptSection("late", "runtime", "LATE"),)))
            await manager.event_bus.drain()
            await manager.drain_plugin("prompt")
            assert consumer.results.empty()
            assert consumer.inputs.empty()
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


@pytest.mark.parametrize("required,status", [((), "completed"), (("producer",), "failed")])
def test_silent_producer_has_bounded_readiness(tmp_path, required, status):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path, required=required, timeout=0.02)
        try:
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            result = await asyncio.wait_for(consumer.results.get(), 1)
            assert result.status == status
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


def test_static_refresh_and_live_updates_are_separate(tmp_path):
    async def run():
        (tmp_path / "AGENTS.md").write_text("OLD")
        manager, plugin, producer, consumer, channels = await setup(tmp_path, sources=())
        try:
            first = (await consumer.request(PromptReadRequest())).unwrap().system_prompt
            (tmp_path / "AGENTS.md").write_text("NEW")
            assert (await consumer.request(PromptReadRequest())).system_prompt == first
            refreshed = (await consumer.request(PromptReadRequest(reason="refresh"))).unwrap().system_prompt
            assert "NEW" in refreshed and "OLD" not in refreshed
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            result = await asyncio.wait_for(consumer.results.get(), 1)
            assert result.status == "completed"
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


def test_early_contribution_is_joined_and_finished_task_cannot_leak(tmp_path):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            await producer.publish(PromptContributionEvent(task_id="task", status="completed", sections=(PromptSection("early", "mode", "EARLY"),)))
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            result = await asyncio.wait_for(consumer.results.get(), 1)
            assert "EARLY" in result.context_blocks[0].content
            await producer.publish(InputFinishedEvent(task_id="task", status="completed"))
            await producer.publish(PromptUpdateEvent(task_id="task", sections=(PromptSection("late", "runtime", "STALE"),)))
            await manager.event_bus.drain()
            await manager.drain_plugin("prompt")
            assert consumer.inputs.empty()
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())
