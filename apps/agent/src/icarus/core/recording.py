"""Recording ports separate from value hooks and business event transport."""

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeRecord:
    event_id: str
    occurred_at: datetime
    run_id: str | None
    name: str
    phase: str
    context: Mapping[str, Any]
    data: Mapping[str, Any]


class RecordSink(Protocol):
    def record(self, point: str, phase: str, *, data: Mapping[str, object]) -> None: ...


class RecordingBackend(Protocol):
    def start(self) -> None: ...
    def offer(self, record: RuntimeRecord) -> bool: ...
    def drain(self) -> None: ...
    def stop(self, drain: bool = True) -> None: ...


_current_sink: ContextVar[RecordSink | None] = ContextVar("icarus_record_sink", default=None)


def record(point: str, phase: str, data: Mapping[str, object] | None = None) -> None:
    sink = _current_sink.get()
    if sink is not None:
        sink.record(point, phase, data=data or {})


def bind_record_sink(sink: RecordSink):
    return _current_sink.set(sink)


def reset_record_sink(token) -> None:
    _current_sink.reset(token)
