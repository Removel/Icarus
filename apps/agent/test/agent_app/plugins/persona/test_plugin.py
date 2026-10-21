import asyncio

import pytest

from agent_app.contracts.prompt import PromptSection
from agent_app.plugins.prompt.plugin import PromptPlugin
from agent_app.plugins.prompt.config import PluginConfig
from icarus.core.agents import AgentCompletedEvent, AgentMessageCompletedEvent, AgentResponse
from icarus.core.contracts.input import UserInputEvent
from icarus.core.models.messages import Message, TextPart
from icarus.core.run_control import TaskChannelRegistry
from icarus.core.tools.result_budget import conservative_token_count


def user(number):
    return UserInputEvent(task_id=f"t{number}", prompt=f"USER-{number}")


def completed(number):
    return AgentCompletedEvent(task_id=f"t{number}", step=9,
        response=AgentResponse(message=Message("assistant", [TextPart(f"ASSISTANT-{number}")])))


@pytest.mark.parametrize("score,expected", [(0.49, None), (0.5, "persona-defense"), (0.79, "persona-defense"), (0.8, "persona-defense")])
def test_attack_thresholds_and_terminal_contribution(make_persona, judge, score, expected):
    async def run():
        plugin, published = make_persona()
        judge.attack = score
        await plugin.on_session_start()
        await plugin.on_input("user-input", user(1))
        assert len(published) == 1 and published[0].status == "completed"
        assert [s.section_id for s in published[0].sections] == ([expected] if expected else [])
        if score >= 0.8:
            assert "拒绝" in published[0].sections[0].content
        assert judge.attacks == ["USER-1"]
    asyncio.run(run())


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("private"), RuntimeError("unexpected")])
def test_judge_failure_always_publishes_completed(make_persona, judge, error):
    async def run():
        plugin, published = make_persona()
        judge.error = error
        await plugin.on_input("user-input", user(1))
        assert published[0].sections == () and published[0].status == "completed"
    asyncio.run(run())


def test_drift_every_five_turns_uses_final_assistant_text_only(make_persona, judge):
    async def run():
        plugin, published = make_persona()
        judge.drift = 0.8
        await plugin.on_session_start()
        for turn in range(1, 12):
            await plugin.on_input("user-input", user(turn))
            await plugin.on_completed("agent", completed(turn))
        assert len(judge.drifts) == 2
        assert all("USER" not in text for call in judge.drifts for text in call[0])
        assert judge.drifts[0][0] == tuple(f"ASSISTANT-{i}" for i in range(1, 5))
        hits = [event.task_id for event in published if any(s.section_id == "persona-reanchor" for s in event.sections)]
        assert hits == ["t5", "t10"]
    asyncio.run(run())


def test_partial_messages_not_subscribed_or_counted(make_persona):
    plugin, _ = make_persona()
    assert AgentMessageCompletedEvent not in plugin.handler_bindings()


def test_detection_runs_in_parallel_and_failure_is_independent(make_persona, judge):
    async def run():
        plugin, published = make_persona()
        await plugin.on_session_start()
        for turn in range(1, 5):
            await plugin.on_input("user-input", user(turn))
            await plugin.on_completed("agent", completed(turn))
        attack_started, drift_started = asyncio.Event(), asyncio.Event()
        async def attack(text):
            attack_started.set()
            await drift_started.wait()
            raise TimeoutError
        async def drift(recent, reference):
            drift_started.set()
            await attack_started.wait()
            return 0.9
        judge.attack_confidence = attack
        judge.drift_confidence = drift
        await asyncio.wait_for(plugin.on_input("user-input", user(5)), 0.3)
        assert [s.section_id for s in published[-1].sections] == ["persona-reanchor"]
    asyncio.run(run())


def test_dynamic_budget_keeps_defense_before_reanchor(make_persona, judge):
    async def run():
        plugin, published = make_persona(max_dynamic_tokens=110)
        await plugin.on_session_start()
        judge.attack, judge.drift = 0.9, 0.9
        for turn in range(1, 6):
            await plugin.on_input("user-input", user(turn))
            await plugin.on_completed("agent", completed(turn))
        assert [s.section_id for s in published[-1].sections] == ["persona-defense"]
        text = "\n\n".join(f"## contributor/persona/{s.section_id}\n{s.content}" for s in published[-1].sections)
        assert conservative_token_count(text) <= 110
    asyncio.run(run())


def test_empty_completed_contribution_finalizes_prompt_without_deadline(make_persona, tmp_path):
    async def run():
        plugin, published = make_persona()
        await plugin.on_session_start()
        prompt = PromptPlugin("prompt", workspace_path=tmp_path, config=PluginConfig(),
            instructions=None, task_channels=TaskChannelRegistry(), logger=plugin.logger)
        output = []
        async def publish(event):
            output.append(event)
        prompt.bind_publisher(publish)
        prompt.prepare({"persona": plugin.static_prompt_sections()}, dynamic_sources=frozenset({"persona"}),
                       update_sources=frozenset(), required_sources=frozenset({"persona"}),
                       measure_dynamic=lambda system, text: len(system) + len(text))
        await plugin.on_input("user-input", user(1))
        await prompt.on_contribution("persona", published[0])
        await prompt.on_input("user-input", user(1))
        assert len(output) == 1 and output[0].status == "completed"
        await prompt.stop()
    asyncio.run(run())


def test_failed_judge_is_fail_open_even_with_zero_thresholds(make_persona, judge):
    async def run():
        plugin, published = make_persona(attack_defend_threshold=0, attack_refuse_threshold=0, drift_threshold=0)
        await plugin.on_session_start()
        judge.error = TimeoutError()
        for turn in range(1, 6):
            await plugin.on_input("user-input", user(turn))
            await plugin.on_completed("agent", completed(turn))
        assert all(event.sections == () for event in published)
    asyncio.run(run())


def test_cancelled_input_backlog_does_not_delay_next_live_task(make_persona, judge):
    async def run():
        plugin, published = make_persona()
        plugin.task_channels = TaskChannelRegistry()
        await plugin.on_session_start()
        pending = []
        for turn in range(1, 6):
            event = user(turn)
            plugin.task_channels.create(event.task_id)
            plugin.task_channels.request_cancel(event.task_id)
            plugin.task_channels.finish(event.task_id)
            pending.append(event)
        live = user(6)
        plugin.task_channels.create(live.task_id).mark_preparing_context()
        for event in (*pending, live):
            await plugin.on_input("user-input", event)
        assert judge.attacks == ["USER-6"]
        assert published[-1].task_id == "t6" and published[-1].status == "completed"
    asyncio.run(run())


def test_duplicate_events_do_not_advance_detection_clock(make_persona, judge):
    async def run():
        plugin, published = make_persona()
        await plugin.on_session_start()
        for _ in range(5):
            await plugin.on_input("user-input", user(1))
            await plugin.on_completed("agent", completed(1))
        assert len(published) == 1
        assert judge.drifts == []
    asyncio.run(run())
