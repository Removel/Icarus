"""One independent, tool-free model call to extract grounded style candidates."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, TypeAdapter

from agent_app.contracts.persona import PersonaSnapshot
from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.prompts import EXTRACTION_PROMPT
from icarus.core.llm import BaseLLM
from icarus.core.models.messages import Message, TextPart
from icarus.core.tools.result_budget import conservative_token_count


class PersonaCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    section: Literal["voice", "relationship"]
    note: StrictStr = Field(min_length=1, max_length=300)
    reason: StrictStr = Field(min_length=1, max_length=500)
    evidence_ids: list[StrictStr] = Field(min_length=1, max_length=20)


class PersonaExtractor:
    def __init__(self, config: PersonaConfig, model_provider: Callable[[], BaseLLM],
                 *, max_input_tokens: int | None = None):
        self.config = config
        self.model_provider = model_provider
        self.max_input_tokens = min(config.evolution_max_input_tokens,
                                    max_input_tokens if max_input_tokens is not None else config.evolution_max_input_tokens)

    async def extract(self, snapshot: PersonaSnapshot, *, persona: str, existing: tuple[str, ...]) -> tuple[PersonaCandidate, ...]:
        system = EXTRACTION_PROMPT.format(limit=self.config.evolution_max_candidates,
            note_limit=self.config.evolution_max_note_chars, reason_limit=self.config.evolution_max_reason_chars)
        evidence = [{"id": item.id, **asdict(item)} for item in snapshot.messages]
        payload = json.dumps({"persona": persona, "existing": existing, "dialogue": evidence}, ensure_ascii=False)
        if conservative_token_count(system + payload) + 128 > self.max_input_tokens:
            raise ValueError("input_too_large")
        model = self.model_provider()
        try:
            async with asyncio.timeout(self.config.evolution_timeout_seconds):
                response = await model.ainvoke([
                    Message("system", [TextPart(system)]), Message("user", [TextPart(payload)]),
                ])
            if response.message.tool_calls or response.finish_reason in {"length", "max_tokens"}:
                raise ValueError("invalid_candidates")
            text = "".join(part.text for part in response.message.content if isinstance(part, TextPart))
            if len(text) > 65536:
                raise ValueError("invalid_candidates")
            rows = TypeAdapter(list[PersonaCandidate]).validate_json(text, strict=True)
            if len(rows) > self.config.evolution_max_candidates:
                raise ValueError("invalid_candidates")
            by_id = {item.id: item for item in snapshot.messages}
            for row in rows:
                if (not row.note.strip() or not row.reason.strip()
                        or len(row.note) > self.config.evolution_max_note_chars
                        or len(row.reason) > self.config.evolution_max_reason_chars
                        or any(key not in by_id for key in row.evidence_ids)
                        or not any(by_id[key].role == "user" for key in row.evidence_ids)):
                    raise ValueError("invalid_candidates")
            return tuple(rows)
        except Exception as error:
            if isinstance(error, ValueError) and str(error) in {"input_too_large", "invalid_candidates"}:
                raise
            raise ValueError("extraction_failed") from None
        finally:
            await model.aclose()
