import asyncio
from dataclasses import replace

import pytest

from agent_app.plugins.persona.judge import EvolutionVerdict, JudgeUnavailable
from agent_app.plugins.persona.tools import PersonaEvolveTool
from icarus.core.contracts.input import UserInputEvent
from icarus.core.models.messages import Message, TextPart, ToolCall
from icarus.runtime.loop_runner import RuntimeLoopRunner


ARGS = {"section": "voice", "note": "I use warmer greetings.", "reason": "User appreciated my warmer greetings."}


@pytest.mark.parametrize("arguments", [
    {}, {"section": "soul", "note": "new", "reason": "change"},
    {**ARGS, "path": "../../persona.md"}, {**ARGS, "note": " "}, {**ARGS, "reason": ""},
    {**ARGS, "note": "X" * 301}, {**ARGS, "reason": "X" * 501},
    {**ARGS, "note": 123}, {**ARGS, "section": []},
])
def test_tool_rejects_bad_arguments_without_judging(make_persona, judge, arguments):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        result = await PersonaEvolveTool(plugin).ainvoke(arguments)
        assert not result.success
        assert not judge.evolutions
        assert not plugin.store.overlay_path.exists()
    asyncio.run(run())


def test_accepted_note_is_persisted_with_reason_and_session(make_persona, judge):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        await plugin.on_input("user-input", UserInputEvent(task_id="t", prompt="I appreciate your warmer greetings"))
        before = plugin.static_prompt_sections()
        result = await PersonaEvolveTool(plugin).ainvoke(ARGS, task_id="t", run_id="r")
        assert result.success and result.output["status"] == "accepted"
        entries = plugin.store.read_entries()
        assert len(entries) == 1
        assert entries[0].note == ARGS["note"] and entries[0].reason == ARGS["reason"]
        assert entries[0].session_id == "session"
        assert plugin.static_prompt_sections() == before
        assert "persona_evolve" in before[-1].content
        assert not PersonaEvolveTool(plugin).can_run_parallel(ARGS)
    asyncio.run(run())


@pytest.mark.parametrize("question", ["grounded", "in_scope", "worth_persisting"])
def test_each_failed_question_rejects_specific_reason(make_persona, judge, question):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        judge.verdict = replace(judge.verdict, **{question: 0.59})
        result = await PersonaEvolveTool(plugin).ainvoke(ARGS)
        assert result.success and result.output == {"status": "rejected", "reason": question}
        assert not plugin.store.overlay_path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("error", [JudgeUnavailable("unavailable"), RuntimeError("SECRET")])
def test_judge_failure_rejects_without_writing(make_persona, judge, error):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        judge.error = error
        result = await PersonaEvolveTool(plugin).ainvoke(ARGS)
        assert result.output == {"status": "rejected", "reason": "judge_unavailable"}
        assert not plugin.store.overlay_path.exists()
    asyncio.run(run())


def test_disabled_tool_remains_registered_but_rejects(make_persona):
    async def run():
        plugin, _ = make_persona()
        plugin.config = plugin.config.model_copy(update={"jev_enabled": False})
        await plugin.on_session_start()
        assert [t.definition.name for t in plugin.build_tools()] == ["persona_evolve"]
        result = await plugin.build_tools()[0].ainvoke(ARGS)
        assert result.output == {"status": "rejected", "reason": "judge_disabled"}
        assert "persona_evolve" not in plugin.static_prompt_sections()[-1].content
    asyncio.run(run())


def test_rate_limit_and_rejected_calls_do_not_consume_it(make_persona, judge):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        tool = PersonaEvolveTool(plugin)
        judge.verdict = EvolutionVerdict(0.1, 0.9, 0.9)
        assert (await tool.ainvoke(ARGS)).output["status"] == "rejected"
        judge.verdict = EvolutionVerdict(0.9, 0.9, 0.9)
        assert (await tool.ainvoke(ARGS)).output["status"] == "accepted"
        assert (await tool.ainvoke(ARGS)).output["reason"] == "rate_limited"
        for turn in range(1, 11):
            await plugin.on_input("user-input", UserInputEvent(task_id=f"t{turn}", prompt="hello"))
        assert (await tool.ainvoke({**ARGS, "note": "I use clearer greetings."})).output["status"] == "accepted"
    asyncio.run(run())


def test_current_task_messages_ground_evolution_without_tool_results(make_persona, judge):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        await PersonaEvolveTool(plugin).ainvoke(ARGS, task_messages=(
            Message("user", [TextPart("<plugin_context>PRIVATE-MEMORY</plugin_context>\n<user_request>Warm greetings please.</user_request>")]),
            Message("tool", [TextPart("PRIVATE-TOOL")], tool_call_id="a"),
            Message("assistant", [TextPart("I like greeting you warmly.")]),
        ))
        recent = judge.evolutions[0]["recent"]
        assert ("user", "Warm greetings please.") in recent
        assert ("assistant", "I like greeting you warmly.") in recent
        assert "PRIVATE" not in str(recent)
    asyncio.run(run())


def test_literal_user_request_tags_do_not_truncate_real_evidence(make_persona, judge):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        original = "Please review <user_request> in this literal example."
        await PersonaEvolveTool(plugin).ainvoke(ARGS, task_messages=(
            Message("user", [TextPart("<plugin_context>PRIVATE</plugin_context>\n\n<user_request>\n" + original + "\n</user_request>")]),))
        recent = judge.evolutions[0]["recent"]
        assert ("user", original) in recent
        assert "PRIVATE" not in str(recent)
    asyncio.run(run())


def test_intermediate_tool_call_text_cannot_erase_grounding_evidence(make_persona, judge):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        messages = [Message("user", [TextPart("I appreciate your warm greetings.")])]
        messages.extend(Message("assistant", [TextPart(f"PLANNING-{i}")],
            tool_calls=[ToolCall(f"call-{i}", "read", {"path": "notes"})]) for i in range(10))
        await PersonaEvolveTool(plugin).ainvoke(ARGS, task_messages=tuple(messages))
        recent = judge.evolutions[0]["recent"]
        assert ("user", "I appreciate your warm greetings.") in recent
        assert "PLANNING" not in str(recent)
    asyncio.run(run())


@pytest.mark.parametrize("cancel_at", ["before", "during_judgment"])
def test_cancelled_task_cannot_persist_global_persona(make_persona, judge, cancel_at):
    async def run():
        from icarus.core.run_control import TaskChannelRegistry
        plugin, _ = make_persona()
        plugin.task_channels = TaskChannelRegistry()
        channel = plugin.task_channels.create("task")
        channel.mark_preparing_context()
        channel.start_run("run")
        await plugin.on_session_start()
        if cancel_at == "before":
            channel.request_cancel("stop")
        else:
            async def verdict(**kwargs):
                channel.request_cancel("stop")
                return EvolutionVerdict(0.9, 0.9, 0.9)
            judge.evolution_verdict = verdict
        result = await PersonaEvolveTool(plugin).ainvoke(ARGS, task_id="task")
        assert result.output == {"status": "rejected", "reason": "task_cancelled"}
        assert not plugin.store.overlay_path.exists()
    asyncio.run(run())


def test_sync_tool_uses_same_runtime_loop(make_persona):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        plugin._loop_runner = RuntimeLoopRunner()
        plugin._loop_runner.bind()
        try:
            result = await asyncio.to_thread(PersonaEvolveTool(plugin).invoke, ARGS)
            assert result.success and result.output["status"] == "accepted"
            assert len(plugin.store.read_entries()) == 1
        finally:
            plugin._loop_runner.close()
    asyncio.run(run())


def test_concurrent_evolutions_across_sessions_do_not_lose_writes(make_persona):
    async def run():
        first, _ = make_persona()
        second, _ = make_persona()
        await first.on_session_start()
        await second.on_session_start()
        results = await asyncio.gather(PersonaEvolveTool(first).ainvoke(ARGS),
            PersonaEvolveTool(second).ainvoke({**ARGS, "note": "I am more concise."}))
        assert all(result.output["status"] == "accepted" for result in results)
        assert len(first.store.read_entries()) == 2
    asyncio.run(run())


def test_write_failure_does_not_claim_acceptance_or_consume_limit(make_persona, monkeypatch):
    async def run():
        plugin, _ = make_persona()
        await plugin.on_session_start()
        tool = PersonaEvolveTool(plugin)
        original = plugin.store.append
        async def fail(entry):
            raise OSError("PRIVATE")
        monkeypatch.setattr(plugin.store, "append", fail)
        result = await tool.ainvoke(ARGS)
        assert not result.success and "PRIVATE" not in result.error
        monkeypatch.setattr(plugin.store, "append", original)
        assert (await tool.ainvoke(ARGS)).output["status"] == "accepted"
    asyncio.run(run())
