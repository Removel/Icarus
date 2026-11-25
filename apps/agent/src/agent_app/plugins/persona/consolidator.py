"""Persona-owned extraction, independent validation and conservative commit."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import math
import unicodedata
from uuid import uuid4

from agent_app.contracts.persona import PersonaConsolidationResult, PersonaSnapshot
from agent_app.plugins.persona.extractor import PersonaExtractor
from agent_app.plugins.persona.judge import Judge
from agent_app.plugins.persona.store import EvolvedEntry, PersonaStore
from icarus.core.llm import BaseLLM
from icarus.core.tools.result_budget import conservative_token_count


def normalized_note(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class PersonaConsolidator:
    def __init__(self, store: PersonaStore, model_provider: Callable[[], BaseLLM],
                 judge_provider: Callable[[], Judge], *, max_input_tokens: int | None = None):
        self.store = store
        self.extractor = PersonaExtractor(store.config, model_provider, max_input_tokens=max_input_tokens)
        self.judge_provider = judge_provider

    async def consolidate(self, snapshot: PersonaSnapshot) -> PersonaConsolidationResult:
        processed = self.store.processed_through(snapshot.identity)
        version = self.store.version()
        if processed >= snapshot.end_sequence:
            return await self.store.commit_consolidation(snapshot, (), expected_version=version)
        if processed != snapshot.start_sequence:
            raise ValueError("persona_changed")
        persona = self.store.persona_text()
        existing = tuple(entry.note for entry in self.store.read_entries(strict=True))
        if not any(item.role == "user" for item in snapshot.messages):
            return await self.store.commit_consolidation(snapshot, (), expected_version=version)
        candidates = await self.extractor.extract(snapshot, persona=persona, existing=existing)
        accepted: list[EvolvedEntry] = []
        seen = {normalized_note(note) for note in existing}
        skipped = 0
        judge = self.judge_provider() if candidates else None
        try:
            for candidate in candidates:
                key = normalized_note(candidate.note)
                if key in seen:
                    skipped += 1
                    continue
                seen.add(key)
                # Keep full cycle context: a nearby qualification may make a correction temporary.
                recent = [(item.role, item.text) for item in snapshot.messages]
                previous = (*existing, *(entry.note for entry in accepted))
                if conservative_token_count(persona + str(recent) + str(previous)) > self.store.config.evolution_max_input_tokens:
                    raise ValueError("input_too_large")
                assert judge is not None
                try:
                    verdict = await asyncio.wait_for(judge.evolution_verdict(
                        note=candidate.note.strip(), reason=candidate.reason.strip(), section=candidate.section,
                        recent=recent, invariants=persona, existing=previous,
                    ), self.store.config.jev_timeout_seconds)
                    scores = (verdict.grounded, verdict.in_scope, verdict.worth_persisting)
                    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in scores):
                        raise ValueError("Invalid verdict")
                except Exception:
                    raise ValueError("judge_unavailable") from None
                if min(scores) < self.store.config.evolution_min_probability:
                    skipped += 1
                    continue
                accepted.append(EvolvedEntry("e_" + uuid4().hex, candidate.section,
                    candidate.note.strip(), candidate.reason.strip(), datetime.now(UTC).isoformat(),
                    snapshot.identity.session_id, tuple(candidate.evidence_ids), snapshot.identity.workspace_key))
            return await self.store.commit_consolidation(snapshot, tuple(accepted),
                expected_version=version, skipped_count=skipped)
        finally:
            if judge is not None:
                await judge.aclose()
