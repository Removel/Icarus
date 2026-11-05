"""Reduce durable admission evidence; absence of execution logs proves nothing."""
from dataclasses import dataclass
from typing import Literal

from icarus.core.runtime_update import RuntimeUpdate

Phase = Literal['prepared', 'submitting', 'not_admitted', 'accepted', 'unknown']


@dataclass(frozen=True)
class SubmissionState:
    phase: Phase = 'unknown'
    attempt: int = 0
    retryable: bool = False
    error_code: str | None = None


def submission_state(records: tuple[RuntimeUpdate, ...], *, task_id: str) -> SubmissionState:
    attempts: dict[int, SubmissionState] = {}
    for record in records:
        if record.task_id != task_id or record.type != 'argus.submission':
            continue
        value = record.payload
        attempt, phase = value.get('attempt'), value.get('phase')
        if type(attempt) is not int or attempt < 0 or phase not in {
            'prepared', 'submitting', 'not_admitted', 'accepted', 'unknown'
        }:
            return SubmissionState(error_code='admission_evidence_invalid')
        old = attempts.get(attempt)
        new = SubmissionState(phase, attempt,
            phase == 'not_admitted' and value.get('retryable') is True,
            value.get('error_code'))
        if old and old.phase in {'accepted', 'unknown', 'not_admitted'}:
            if phase in {'prepared', 'submitting'} or old == new:
                continue
            if old.phase != phase:
                new = SubmissionState('unknown', attempt, error_code='admission_evidence_conflict')
        attempts[attempt] = new
    if not attempts:
        return SubmissionState()
    latest = attempts[max(attempts)]
    # A safe rejection of one attempt never disproves another attempt's admission.
    if any(s.phase in {'submitting', 'unknown'} for s in attempts.values()):
        return SubmissionState('unknown', latest.attempt, error_code=latest.error_code)
    if any(s.phase == 'accepted' for s in attempts.values()):
        return SubmissionState('accepted', latest.attempt)
    return latest
