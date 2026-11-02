"""Argus business notifications. Persistence is authoritative, notifications replayable."""
from dataclasses import dataclass
from icarus.core.events import Event


@dataclass(frozen=True, kw_only=True)
class ObservationRecorded(Event):
    observation_id: str
    source_key: str


@dataclass(frozen=True, kw_only=True)
class RulePassed(Event):
    channel_id: str


EVENTS = {'argus.observation_recorded': ObservationRecorded, 'argus.rule_passed': RulePassed}
CAPABILITIES = ()
