import asyncio
import logging

import pytest

from agent_app.application.session_runtime import SessionRuntime
from agent_app.config import ConfigModel, LLMConfig, ModelSettings, ThinkMode
from agent_app.plugins.memory.mem0_http_adapter import Mem0HttpAdapter
from agent_app.plugins.memory.models import MemoryRecallResult
from agent_app.plugins.persona.judge import EvolutionVerdict
from icarus.core.agents import AgentCompletedEvent, AgentMessageCompletedEvent, AgentResponse
from icarus.core.identity import SessionIdentity
from icarus.core.models.messages import Message, TextPart


def config(data_dir, **persona):
    model = LLMConfig(model_name="test", context_window=128000, max_tokens=1024,
                      temperature=0, default_think_level=ThinkMode.LOW)
    return ConfigModel(openai_base_url="https://test.example/v1", anthropic_base_url="https://test.example",
        icarus_data_dir=data_dir, model_settings=ModelSettings(thinking=model, perception=model),
        runtime={"plugin_config": {
            "memory": {"user_id": "test-user", "agent_id": "icarus"},
            "knowledge": {"knowledge_base": "test"},
            "prompt": {"required_contributors": ["persona"]},
            "persona": persona,
        }})


def test_real_runtime_routes_persona_and_pins_snapshot_across_sessions(tmp_path, monkeypatch, judge):
    async def run():
        monkeypatch.setattr("agent_app.plugins.persona.plugin.create_judge", lambda config: judge)
        async def recall(self, query, **kwargs):
            return MemoryRecallResult((), query)
        monkeypatch.setattr(Mem0HttpAdapter, "arecall", recall)
        updates, captured = [], []
        finished = asyncio.Event()
        async def publish(update):
            updates.append(update)
            if update.type == "task.finished":
                finished.set()
        class CaptureAgent:
            async def astream(self, *, system_prompt, history_messages, input_prompt, input_images, tools, run_control):
                captured.append((system_prompt, input_prompt))
                message = Message("assistant", [TextPart("A warm and clear reply.")])
                yield AgentMessageCompletedEvent(step=1, message=message)
                yield AgentCompletedEvent(step=1, response=AgentResponse(message=message,
                    messages=[Message("user", [TextPart(input_prompt)]), message], task_message_start=0))
        first = SessionRuntime(SessionIdentity.create(tmp_path, "s1"),
            config=config(tmp_path / "data", jev_enabled=True), publish_update=publish)
        await first.start()
        original = first.runtime_host.get_plugin("prompt")._system_prompt
        first.runtime_host.get_plugin("agent").agent_factory.get_agent = lambda role: CaptureAgent()
        persona = first.runtime_host.get_plugin("persona")
        assert "persona-01-identity" in original
        assert "persona_evolve" in first.tool_registry.names()
        try:
            for turn in range(1, 7):
                finished.clear()
                judge.attack = 0.7 if turn == 2 else 0
                judge.drift = 0.9
                await first.submit(f"test input {turn}")
                await asyncio.wait_for(finished.wait(), 3)
            assert "persona-defense" in captured[1][1]
            assert "persona-reanchor" in captured[4][1]
            assert "persona-reanchor" not in captured[5][1]
            assert all(system == original for system, _ in captured)
            result = await persona.build_tools()[0].ainvoke({"section": "voice", "note": "I greet with warmth.",
                                                           "reason": "Repeated positive conversation."})
            assert result.output["status"] == "accepted"
            second = SessionRuntime(SessionIdentity.create(tmp_path, "s2"),
                config=config(tmp_path / "data"), publish_update=publish)
            await second.start()
            try:
                assert "I greet with warmth." in second.runtime_host.get_plugin("prompt")._system_prompt
                assert first.runtime_host.get_plugin("prompt")._system_prompt == original
            finally:
                await second.stop("test")
        finally:
            await first.stop("test")
        assert len([u for u in updates if u.type == "task.finished"]) == 6
    asyncio.run(run())


def test_optional_missing_persona_disables_plugin_without_blocking_session(tmp_path):
    async def run():
        settings = config(tmp_path / "data", persona_id="missing")
        settings.runtime.required_plugin_ids.remove("persona")
        settings.runtime.plugin_config["prompt"]["required_contributors"] = []
        runtime = SessionRuntime(SessionIdentity.create(tmp_path, "optional"), config=settings,
            publish_update=lambda update: asyncio.sleep(0))
        await runtime.start()
        try:
            assert runtime.is_running
            assert "persona_evolve" not in runtime.tool_registry.names()
        finally:
            await runtime.stop("test")
    asyncio.run(run())
