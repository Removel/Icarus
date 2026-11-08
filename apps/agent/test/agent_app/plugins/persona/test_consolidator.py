import asyncio
from dataclasses import replace
import importlib.util
import json

import pytest

from agent_app.contracts.persona import PersonaEvidence, PersonaSnapshot
from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.store import PersonaStore
from icarus.core.identity import SessionIdentity
from icarus.core.models.messages import LLMResponse, Message, TextPart


class Model:
    def __init__(self, value):
        self.value = value
        self.calls = []
        self.closed = False

    async def ainvoke(self, messages, tools=None):
        assert tools is None
        self.calls.append(messages)
        if isinstance(self.value, Exception):
            raise self.value
        return LLMResponse(Message("assistant", [TextPart(json.dumps(self.value))]))

    async def aclose(self):
        self.closed = True


def snapshot(tmp_path, end=25):
    return PersonaSnapshot(SessionIdentity.create(tmp_path, "source"), "default", 0, end,
        (PersonaEvidence(1, "user", "以后请保持简洁，长期如此", "first", "completed"),
         *(PersonaEvidence(n, "assistant", f"later {n}", f"t{n}", "completed") for n in range(2, end))))


def candidate(**changes):
    return {"section": "voice", "note": "I keep replies concise.",
            "reason": "The user explicitly requested this for future conversations.", "evidence_ids": ["m_1"], **changes}


def test_independent_extraction_preserves_early_evidence_and_deduplicates(tmp_path, judge):
    assert importlib.util.find_spec("agent_app.plugins.persona.consolidator"), "missing independent consolidator"
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        model = Model([candidate(), candidate()])
        store = PersonaStore(tmp_path, "default", PersonaConfig(jev_enabled=True))
        worker = PersonaConsolidator(store, lambda: model, lambda: judge)
        first = await worker.consolidate(snapshot(tmp_path))
        assert first.accepted_count == 1 and first.skipped_count == 1
        assert store.processed_through(snapshot(tmp_path).identity) == 25
        assert "以后请保持简洁" in str(judge.evolutions[0]["recent"])
        assert judge.evolutions[0]["existing"] == ()
        assert model.closed and judge.closed
        second = await worker.consolidate(snapshot(tmp_path))
        assert second.already_processed
        assert len(model.calls) == 1
        assert store.read_entries()[0].evidence_ids == ("m_1",)
    asyncio.run(run())


@pytest.mark.parametrize("value", [
    [candidate(evidence_ids=["m_99"])], [candidate(evidence_ids=["m_2"])],
    [candidate(section="soul")], [candidate(path="persona.md")], RuntimeError("PRIVATE"),
])
def test_invalid_extraction_does_not_advance_progress(tmp_path, judge, value):
    assert importlib.util.find_spec("agent_app.plugins.persona.consolidator"), "missing independent consolidator"
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        model = Model(value)
        store = PersonaStore(tmp_path, "default", PersonaConfig(jev_enabled=True))
        with pytest.raises(ValueError):
            await PersonaConsolidator(store, lambda: model, lambda: judge).consolidate(snapshot(tmp_path))
        assert store.processed_through(snapshot(tmp_path).identity) == 0
        assert not store.overlay_path.exists()
        assert model.closed
    asyncio.run(run())


def test_judge_failure_after_an_accepted_candidate_commits_nothing(tmp_path, judge):
    assert importlib.util.find_spec("agent_app.plugins.persona.consolidator"), "missing independent consolidator"
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        model = Model([candidate(), candidate(note="I use warm language.")])
        original = judge.evolution_verdict
        async def verdict(**kwargs):
            if judge.evolutions:
                raise RuntimeError("PRIVATE")
            return await original(**kwargs)
        judge.evolution_verdict = verdict
        store = PersonaStore(tmp_path, "default", PersonaConfig(jev_enabled=True))
        with pytest.raises(ValueError, match="judge_unavailable"):
            await PersonaConsolidator(store, lambda: model, lambda: judge).consolidate(snapshot(tmp_path))
        assert not store.overlay_path.exists()
        assert judge.closed
    asyncio.run(run())


def test_empty_or_rejected_candidates_are_successful_no_change(tmp_path, judge):
    assert importlib.util.find_spec("agent_app.plugins.persona.consolidator"), "missing independent consolidator"
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        judge.verdict = replace(judge.verdict, in_scope=0.1)
        store = PersonaStore(tmp_path, "default", PersonaConfig(jev_enabled=True))
        result = await PersonaConsolidator(store, lambda: Model([candidate()]), lambda: judge).consolidate(snapshot(tmp_path))
        assert result.status == "no_change"
        assert store.processed_through(snapshot(tmp_path).identity) == 25
    asyncio.run(run())


def test_empty_extraction_commits_progress_without_opening_judge(tmp_path):
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        model = Model([])
        store = PersonaStore(tmp_path, "default", PersonaConfig())
        def no_judge():
            raise AssertionError("Empty extraction must not require a judge")
        result = await PersonaConsolidator(store, lambda: model, no_judge).consolidate(snapshot(tmp_path))
        assert result.status == "no_change" and model.closed
        assert store.processed_through(snapshot(tmp_path).identity) == 25
    asyncio.run(run())


def test_oversized_cycle_fails_before_model_creation_without_truncation(tmp_path):
    from agent_app.plugins.persona.consolidator import PersonaConsolidator
    async def run():
        store = PersonaStore(tmp_path, "default", PersonaConfig(evolution_max_input_tokens=10))
        def no_model():
            raise AssertionError("Oversized input must fail before creating a client")
        with pytest.raises(ValueError, match="input_too_large"):
            await PersonaConsolidator(store, no_model, no_model).consolidate(snapshot(tmp_path))
        assert not store.overlay_path.exists()
    asyncio.run(run())
