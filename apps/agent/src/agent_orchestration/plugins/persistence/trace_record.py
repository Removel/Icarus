"""HookEvent 到单行 Trace JSON 的转换。"""

from dataclasses import dataclass
import json

from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor


ROUTING_CONTEXT_FIELDS = frozenset(
    {
        "workspace_key",
        "workspace_path",
        "session_id",
        "correlation_id",
    }
)


@dataclass(frozen=True)
class TraceRecord:
    schema_version: int
    record_type: str
    event_id: str
    occurred_at: str
    correlation_id: str | None
    run_id: str | None
    name: str
    phase: str
    context: dict
    data: dict

    @classmethod
    def from_hook_event(
        cls,
        event: HookEvent,
        redactor: Redactor,
    ) -> "TraceRecord":
        context = {
            key: value
            for key, value in event.context.items()
            if key not in ROUTING_CONTEXT_FIELDS
        }
        correlation_id = event.context.get("correlation_id")
        return cls(
            schema_version=1,
            record_type="hook_event",
            event_id=event.event_id,
            occurred_at=event.occurred_at.isoformat(),
            correlation_id=(
                str(correlation_id)
                if correlation_id is not None
                else None
            ),
            run_id=event.run_id,
            name=event.name,
            phase=event.phase,
            context=redactor.redact(context),
            data=redactor.redact(dict(event.data)),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type,
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "correlation_id": self.correlation_id,
            "run_id": self.run_id,
            "name": self.name,
            "phase": self.phase,
            "context": self.context,
            "data": self.data,
        }

    def to_json_line(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @property
    def estimated_bytes(self) -> int:
        return len((self.to_json_line() + "\n").encode("utf-8"))
