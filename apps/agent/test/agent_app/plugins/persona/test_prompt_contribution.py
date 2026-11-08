import asyncio
import logging

import pytest

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.plugin import PersonaPlugin
from agent_app.plugins.persona.store import EvolvedEntry, PersonaFileMissing, PersonaStore


def plugin(tmp_path, **settings):
    return PersonaPlugin("persona", config=PersonaConfig(**settings), data_dir=tmp_path,
                         session_id="session", logger=logging.getLogger("test.persona"))


def test_static_sections_are_frozen_and_new_session_loads_evolution(tmp_path):
    async def run():
        first = plugin(tmp_path)
        second = plugin(tmp_path)
        await first.on_session_start()
        before = first.static_prompt_sections()
        assert [s.section_id for s in before] == ["persona-01-identity"]
        assert all(s.layer == "persona" for s in before)
        await PersonaStore(tmp_path, "default", PersonaConfig()).append(
            EvolvedEntry("e_a", "voice", "I use warmer greetings.", "PRIVATE-REASON", "2026-10-20T12:00:00Z", "PRIVATE-SESSION"))
        await second.on_session_start()
        assert first.static_prompt_sections() == before
        text = "\n".join(s.content for s in second.static_prompt_sections())
        assert "I use warmer greetings." in text
        captured = []
        async def publish(event):
            captured.append(event)
        second.publish = publish
        from icarus.core.contracts.input import UserInputEvent
        await second.on_input("user-input", UserInputEvent(task_id="task", prompt="hello"))
        assert captured[0].sections == ()
        assert "PRIVATE" not in text
        assert "persona_evolve" not in text
        await first.stop()
        await second.stop()
    asyncio.run(run())


def test_relationship_snapshot_is_refreshed_for_each_task_and_never_in_system(tmp_path):
    async def run():
        value = plugin(tmp_path)
        calls = []
        captured = []
        async def load():
            calls.append(True)
            return f"PROFILE-{len(calls)}"
        async def publish(event):
            captured.append(event)
        value.relationship.load = load
        value.publish = publish
        await value.on_session_start()
        assert not calls
        static = value.static_prompt_sections()
        from icarus.core.contracts.input import UserInputEvent
        for number in (1, 2):
            await value.on_input("user-input", UserInputEvent(task_id=f"t{number}", prompt="hello"))
            assert captured[-1].sections[0].layer == "contributor"
            assert captured[-1].sections[0].content == f"PROFILE-{number}"
        assert value.static_prompt_sections() == static
        assert "PROFILE" not in "".join(s.content for s in static)
        await value.stop()
    asyncio.run(run())


def test_missing_persona_fails_start(tmp_path):
    async def run():
        value = plugin(tmp_path, persona_id="missing")
        with pytest.raises(PersonaFileMissing):
            await value.on_session_start()
        await value.stop()
    asyncio.run(run())


@pytest.mark.parametrize("enabled,live_judge", [(False, False), (True, False), (True, True), (False, True)])
def test_main_agent_has_no_evolution_tool_or_submission_guidance(tmp_path, enabled, live_judge):
    async def run():
        from apps.agent.test.agent_app.plugins.persona.conftest import FakeJudge
        value = plugin(tmp_path, jev_enabled=enabled)
        if live_judge:
            value.judge = FakeJudge()
        await value.on_session_start()
        sections = value.static_prompt_sections()
        assert all(s.section_id != "persona-04-policy" for s in sections)
        guidance = [s for s in sections if s.section_id == "persona-05-evolution"]
        assert guidance == []
        assert value.build_tools() == ()
        await value.stop()
    asyncio.run(run())
