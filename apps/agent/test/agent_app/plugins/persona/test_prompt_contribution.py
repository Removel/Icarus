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
        assert [s.section_id for s in before] == ["persona-01-identity", "persona-04-policy"]
        assert all(s.layer == "persona" for s in before)
        await PersonaStore(tmp_path, "default", PersonaConfig()).append(
            EvolvedEntry("e_a", "voice", "I use warmer greetings.", "PRIVATE-REASON", "2026-10-20T12:00:00Z", "PRIVATE-SESSION"))
        await second.on_session_start()
        assert first.static_prompt_sections() == before
        text = "\n".join(s.content for s in second.static_prompt_sections())
        assert "I use warmer greetings." in text
        assert "PRIVATE" not in text
        assert "persona_evolve" not in text
        await first.stop()
        await second.stop()
    asyncio.run(run())


def test_missing_persona_fails_start(tmp_path):
    async def run():
        value = plugin(tmp_path, persona_id="missing")
        with pytest.raises(PersonaFileMissing):
            await value.on_session_start()
        await value.stop()
    asyncio.run(run())


def test_policy_preserves_competence(tmp_path):
    async def run():
        value = plugin(tmp_path)
        await value.on_session_start()
        policy = value.static_prompt_sections()[-1].content
        assert "事实" in policy and "工具" in policy and "合理" in policy
        await value.stop()
    asyncio.run(run())
