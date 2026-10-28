import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from agent_app.contracts.prompt import PromptContributionEvent, PromptSection, PromptUpdateEvent
from agent_app.plugins.blackboard.prompt_composer import BlackboardPromptComposer
from agent_app.plugins.prompt.composer import prompt_context_block
from icarus.core.contracts.input import UserInputEvent
from icarus.core.models.messages import TextPart
from icarus.core.run_control import TaskContextInputResultEvent
from icarus.core.tools.result_budget import conservative_token_count
from apps.agent.test.agent_app.plugins.prompt.test_plugin import setup


async def drained(manager):
    await manager.event_bus.drain()
    await manager.drain_plugin("prompt")
    await manager.drain_plugin("consumer")
    await manager.event_bus.drain()
    await manager.drain_plugin("prompt")


@pytest.mark.parametrize("failed", [False, True])
def test_live_updates_require_successful_preparation_and_running_channel(tmp_path, failed):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path, required=("producer",) if failed else ())
        try:
            channels.get("task").mark_preparing_context()
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            await producer.publish(PromptContributionEvent(task_id="task", status="failed" if failed else "completed"))
            await asyncio.wait_for(consumer.results.get(), 1)
            await producer.publish(PromptUpdateEvent(task_id="task", sections=(PromptSection("n", "runtime", "PREMATURE"),)))
            await drained(manager)
            assert consumer.inputs.empty()
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


def test_forged_acknowledgement_cannot_release_update_reservation(tmp_path):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            channels.get("task").mark_preparing_context()
            assert channels.get("task").start_run("run")
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            await producer.publish(PromptContributionEvent(task_id="task", status="completed"))
            await asyncio.wait_for(consumer.results.get(), 1)
            async def withhold(source, event):
                await consumer.inputs.put(event)
            consumer.on_input = withhold
            await producer.publish(PromptUpdateEvent(task_id="task", sections=(PromptSection("n", "runtime", "PENDING"),)))
            request = await asyncio.wait_for(consumer.inputs.get(), 1)
            await producer.publish(TaskContextInputResultEvent(task_id="task", request_event_id=request.event_id, status="not_running", run_id="run"))
            await drained(manager)
            assert request.event_id in plugin._updates
            await consumer.publish(TaskContextInputResultEvent(task_id="task", request_event_id=request.event_id, status="accepted", run_id="wrong-run"))
            await drained(manager)
            assert request.event_id in plugin._updates
            await consumer.publish(TaskContextInputResultEvent(task_id="task", request_event_id=request.event_id, status="accepted", run_id="run"))
            await drained(manager)
            assert request.event_id not in plugin._updates
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


@pytest.mark.parametrize("early", [False, True])
def test_expired_contribution_never_wins_timer_race(tmp_path, early):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path, required=("producer",), timeout=0.02)
        try:
            input_event = UserInputEvent(task_id="task", prompt="hello", occurred_at=datetime.now(UTC) - timedelta(seconds=10))
            contribution = PromptContributionEvent(task_id="task", status="completed", sections=(PromptSection("late", "mode", "AFTER-DEADLINE"),))
            for event in ((contribution, input_event) if early else (input_event, contribution)):
                await producer.publish(event)
            result = await asyncio.wait_for(consumer.results.get(), 1)
            assert result.status == "failed"
            assert not result.context_blocks
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())


def test_separate_runtime_batches_never_exceed_total_prompt_budget(tmp_path):
    async def run():
        manager, plugin, producer, consumer, channels = await setup(tmp_path)
        try:
            plugin.config.max_prompt_tokens = 512
            composer = BlackboardPromptComposer()
            def measure(system, content):
                serialized = composer.serialize_context([prompt_context_block(content)]) if content else ""
                wrapper = f"<plugin_context>\n{serialized}\n</plugin_context>" if serialized else ""
                return conservative_token_count(system) + conservative_token_count(wrapper)
            plugin._measure_dynamic = measure
            channel = channels.get("task")
            channel.mark_preparing_context()
            assert channel.start_run("run")
            await producer.publish(UserInputEvent(task_id="task", prompt="hello"))
            await producer.publish(PromptContributionEvent(task_id="task", status="completed"))
            await asyncio.wait_for(consumer.results.get(), 1)
            batches = []
            async def apply(source, event):
                result = channels.add_context(event.task_id, event.content, source_id=source, event_id=event.event_id)
                batch = channel.drain_context(applied_before_step=2)
                if batch:
                    batches.append(batch)
                await consumer.publish(TaskContextInputResultEvent(task_id="task", request_event_id=event.event_id, status=result.status, run_id="run"))
            consumer.on_input = apply
            for _ in range(8):
                await producer.publish(PromptUpdateEvent(task_id="task", sections=(PromptSection("notice", "runtime", "notice"),)))
                await drained(manager)
            actual = conservative_token_count(plugin._system_prompt) + sum(conservative_token_count("".join(part.text for part in batch.message.content if isinstance(part, TextPart))) for batch in batches)
            assert actual <= 512
            assert batches
        finally:
            await manager.stop(drain=False)
    asyncio.run(run())
