"""Bounded-cardinality recording aggregation; no Hook or EventBus dependency."""

from collections import OrderedDict
from datetime import UTC, datetime
import logging
from threading import RLock
from types import MappingProxyType
from uuid import uuid4

from icarus.core.hooks.hook_context import get_hook_context
from icarus.core.recording import RuntimeRecord, RecordingBackend

logger = logging.getLogger(__name__)


class RuntimeRecorder:
    def __init__(self) -> None:
        self._backends: list[RecordingBackend] = []
        self._counts: OrderedDict[tuple, RuntimeRecord] = OrderedDict()
        self._lock = RLock()
        self._running = False
        self._recording = False
        self._started_backends: list[RecordingBackend] = []

    def register_backend(self, backend) -> None:
        if self._running:
            raise RuntimeError("Recording backends must register before start")
        self._backends.append(backend)

    def start(self) -> None:
        if self._running:
            return
        for backend in self._backends:
            try:
                backend.start()
                self._started_backends.append(backend)
            except Exception:
                logger.exception("Recording backend startup failed")
                try:
                    backend.stop(drain=False)
                except Exception:
                    logger.exception("Failed recording backend cleanup failed")
        self._running = True

    def _offer(self, row) -> None:
        for backend in self._started_backends:
            try:
                backend.offer(row)
            except Exception:
                logger.exception("Recording backend failed")

    def record(self, point: str, phase: str, *, data) -> None:
        if not self._running:
            return
        context = get_hook_context()
        values = dict(context.data) if context else {}
        run_id = context.run_id if context else None
        with self._lock:
            if self._recording:
                return
            self._recording = True
            try:
                if point in {"event.publish", "event.route", "plugin.consume", "plugin.background_work"} and phase != "error":
                    event = data.get("published_event")
                    event_type = type(event.event).__name__ if event is not None else data.get("event_type", "")
                    key = (values.get("workspace_key"), values.get("session_id"), run_id, data.get("plugin_id"), event_type, point, phase)
                    if key not in self._counts and len(self._counts) >= 1024:
                        _, row = self._counts.popitem(last=False)
                        self._offer(row)
                    previous = self._counts.get(key)
                    count = previous.data["count"] + 1 if previous else 1
                    row = RuntimeRecord(uuid4().hex, datetime.now(UTC), run_id, point, phase,
                        MappingProxyType(values), MappingProxyType({"count": count, "plugin_id": data.get("plugin_id"), "event_type": event_type}))
                    self._counts[key] = row
                    if count >= 1024:
                        self._offer(self._counts.pop(key))
                    return
                safe = self._summarize(point, data)
                self._offer(RuntimeRecord(uuid4().hex, datetime.now(UTC), run_id, point, phase,
                    MappingProxyType(values), MappingProxyType(safe)))
                if point.startswith("agent.") and phase in {"after", "error"}:
                    self.flush()
            finally:
                self._recording = False

    @staticmethod
    def _summarize(point, data):
        safe = dict(data)
        for key in ("messages", "history_messages", "input_prompt", "system_prompt", "input_images", "chunks", "response", "stream_result", "result"):
            value = safe.pop(key, None)
            if key in {"response", "stream_result"} and value is not None:
                usage = value.get("usage") if isinstance(value, dict) else getattr(value, "usage", None)
                safe["usage"] = usage
                text = value.get("text", "") if isinstance(value, dict) else ""
                safe["text_chars"] = len(text)
        return safe

    def flush(self) -> None:
        with self._lock:
            for row in self._counts.values():
                self._offer(row)
            self._counts.clear()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self.flush()
            self._running = False
            for backend in reversed(self._started_backends):
                try:
                    backend.stop(drain=True)
                except Exception:
                    logger.exception("Recording backend shutdown failed")
            self._started_backends.clear()
