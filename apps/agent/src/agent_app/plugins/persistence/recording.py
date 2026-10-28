"""JSONL recording backend; no registration with HookRegistry or EventBus."""

from pathlib import Path

from icarus.core.identity import SessionIdentity
from icarus.core.recording import RuntimeRecord
from agent_app.plugins.persistence.trace_record import TraceRecord, ROUTING_CONTEXT_FIELDS
from agent_app.plugins.persistence.trace_writer import TraceWriteRequest


class FileRecordingBackend:
    def __init__(self, writer, redactor) -> None:
        self.writer = writer
        self.redactor = redactor

    def start(self) -> None:
        self.writer.start()

    def offer(self, record: RuntimeRecord) -> bool:
        context = record.context
        if not all(context.get(key) for key in ("workspace_path", "workspace_key", "session_id")):
            return False
        identity = SessionIdentity(Path(str(context["workspace_path"])), str(context["workspace_key"]), str(context["session_id"]))
        row = TraceRecord(
            schema_version=2, record_type="runtime_record", event_id=record.event_id,
            occurred_at=record.occurred_at.isoformat(), task_id=context.get("task_id"),
            run_id=record.run_id, name=record.name, phase=record.phase,
            context=self.redactor.redact({k: v for k, v in context.items() if k not in ROUTING_CONTEXT_FIELDS}),
            data=self.redactor.redact(dict(record.data)),
        )
        return self.writer.offer(TraceWriteRequest(identity=identity, record=row))

    def drain(self) -> None:
        self.writer.drain()

    def stop(self, drain: bool = True) -> None:
        self.writer.stop(drain=drain)
