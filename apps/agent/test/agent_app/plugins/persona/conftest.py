import asyncio
import logging

import pytest

from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.judge import EvolutionVerdict
from agent_app.plugins.persona.plugin import PersonaPlugin


class FakeJudge:
    def __init__(self):
        self.attack = 0.0
        self.drift = 0.0
        self.verdict = EvolutionVerdict(0.9, 0.9, 0.9)
        self.attacks = []
        self.drifts = []
        self.evolutions = []
        self.error = None
        self.closed = False

    async def attack_confidence(self, text):
        self.attacks.append(text)
        if self.error:
            raise self.error
        return self.attack

    async def drift_confidence(self, recent, reference):
        self.drifts.append((tuple(recent), reference))
        if self.error:
            raise self.error
        return self.drift

    async def evolution_verdict(self, **kwargs):
        self.evolutions.append(kwargs)
        if self.error:
            raise self.error
        return self.verdict

    async def aclose(self):
        self.closed = True


@pytest.fixture
def judge():
    return FakeJudge()


@pytest.fixture
def make_persona(tmp_path, judge):
    def make(**settings):
        config = PersonaConfig(jev_enabled=True, **settings)
        plugin = PersonaPlugin("persona", config=config, data_dir=tmp_path,
                               session_id="session", logger=logging.getLogger("test.persona"))
        plugin.judge = judge
        published = []
        async def publish(event):
            published.append(event)
        plugin.bind_publisher(publish)
        return plugin, published
    return make
