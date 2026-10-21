import asyncio
from datetime import UTC, datetime

import pytest

from agent_app.contracts.prompt import PromptReadRequest, PromptSection, PromptUpdateEvent
from apps.agent.test.agent_app.plugins.prompt.test_plugin import setup


def test_runtime_update_injects_user_context_without_mutating_system(tmp_path):
    async def run():
        from icarus.core.contracts.input import UserInputEvent
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            channels.get("task").mark_preparing_context()
            assert channels.get("task").start_run("run")
            from agent_app.contracts.prompt import PromptContributionEvent
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            await producer.publish(PromptContributionEvent(task_id="task", status="completed"))
            await asyncio.wait_for(consumer.results.get(), 1)
            before = (await consumer.request(PromptReadRequest())).system_prompt
            update = PromptUpdateEvent(task_id="task", sections=(PromptSection("notice", "runtime", "LIVE-NOTICE"),))
            await producer.publish(update)
            event = await asyncio.wait_for(consumer.inputs.get(), 1)
            assert "LIVE-NOTICE" in event.content
            assert (await consumer.request(PromptReadRequest())).system_prompt == before
            await producer.publish(update)
            await manager.event_bus.drain()
            await manager.drain_plugin("prompt")
            assert consumer.inputs.empty()
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


def test_optional_invalid_static_contribution_does_not_prevent_session_start(tmp_path):
    from agent_app.plugins.prompt.plugin import PromptPlugin
    from agent_app.plugins.prompt.config import PluginConfig
    from icarus.core.run_control import TaskChannelRegistry
    import logging
    plugin = PromptPlugin("prompt", workspace_path=tmp_path, config=PluginConfig(), instructions=None,
        task_channels=TaskChannelRegistry(), logger=logging.getLogger("test"), base_prompt="BASE")
    plugin.prepare({"broken": (PromptSection("bad", "mode", "WRONG"),)},
        dynamic_sources=frozenset(), update_sources=frozenset(), required_sources=frozenset(), measure_dynamic=lambda s, c: len(s)+len(c))
    assert "BASE" in plugin._system_prompt
    assert "WRONG" not in plugin._system_prompt


def test_rejected_live_update_does_not_consume_budget(tmp_path):
    async def run():
        from icarus.core.contracts.input import UserInputEvent
        from agent_app.contracts.prompt import PromptContributionEvent
        from icarus.core.run_control import TaskContextInputResultEvent
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            channels.get("task").mark_preparing_context()
            assert channels.get("task").start_run("run")
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            await producer.publish(PromptContributionEvent(task_id="task", status="completed"))
            await asyncio.wait_for(consumer.results.get(), 1)
            async def reject(source, event):
                await consumer.inputs.put(event)
                await consumer.publish(TaskContextInputResultEvent(task_id=event.task_id, request_event_id=event.event_id, status="already_finished", run_id="run"))
            consumer.on_input = reject
            before = plugin._tasks["task"].injected_content
            await producer.publish(PromptUpdateEvent(task_id="task", sections=(PromptSection("n", "runtime", "REJECTED"),)))
            await asyncio.wait_for(consumer.inputs.get(), 1)
            await manager.event_bus.drain()
            await manager.drain_plugin("prompt")
            assert plugin._tasks["task"].injected_content == before
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())
