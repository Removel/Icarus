"""Immutable persisted dialogue evidence and persona consolidation outcomes."""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal

from icarus.core.identity import SessionIdentity


def persona_source(identity: SessionIdentity) -> str:
    return json.dumps([identity.workspace_key, identity.session_id], separators=(",", ":"))


@dataclass(frozen=True)
class PersonaEvidence:
    sequence: int
    role: Literal["user", "assistant"]
    text: str
    task_id: str | None = None
    task_status: str | None = None

    @property
    def id(self) -> str:
        return f"m_{self.sequence}"


@dataclass(frozen=True)
class PersonaSnapshot:
    identity: SessionIdentity
    persona_id: str
    start_sequence: int
    end_sequence: int
    messages: tuple[PersonaEvidence, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.start_sequence <= self.end_sequence:
            raise ValueError("Invalid persona history range")
        if any(not self.start_sequence < item.sequence <= self.end_sequence for item in self.messages):
            raise ValueError("Evidence outside persona history range")

    @property
    def cycle_id(self) -> str:
        value = json.dumps([persona_source(self.identity), self.persona_id,
                            self.start_sequence, self.end_sequence])
        return "pc_" + sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class PersonaConsolidationResult:
    cycle_id: str
    status: Literal["applied", "no_change"]
    accepted_count: int = 0
    skipped_count: int = 0
    already_processed: bool = False


@dataclass(frozen=True)
class PersonaConsolidationRequest:
    session_id: str
    status: Literal["accepted", "already_running", "skipped"]
    request_id: str | None = None
    reason: str | None = None
