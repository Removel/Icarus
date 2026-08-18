"""Per-turn Skill matches and ordered Agent tool execution traces."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
import enum
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from apps.agent.src.agent_orchestration.capability import (
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import ImagePart, ToolCall


@dataclass(frozen=True)
class ToolCallTrace:
    """One tool call, positioned by its started-event arrival order."""

    step: int
    tool_call: ToolCall
    result: ToolExecutionResult | None = None
    sequence_index: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tool_call",
            _snapshot_tool_call(self.tool_call),
        )
        if self.result is not None:
            object.__setattr__(
                self,
                "result",
                _snapshot_result(self.result),
            )


@dataclass(frozen=True)
class TurnRecord:
    """Deeply immutable snapshot of one completed or failed Agent turn."""

    correlation_id: str
    prompt: str
    input_images: tuple[ImagePart, ...]
    matched_skills: tuple[SkillDefinition, ...]
    tool_calls: tuple[ToolCallTrace, ...]
    results_by_call_id: Mapping[str, ToolExecutionResult]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_images",
            tuple(
                ImagePart(url=image.url, media_type=image.media_type)
                for image in self.input_images
            ),
        )
        object.__setattr__(
            self,
            "matched_skills",
            tuple(_snapshot_skill(skill) for skill in self.matched_skills),
        )
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(
            self,
            "results_by_call_id",
            MappingProxyType(
                {
                    str(call_id): _snapshot_result(result)
                    for call_id, result in self.results_by_call_id.items()
                }
            ),
        )

    @property
    def started_event_count(self) -> int:
        """Return every accepted started event, including duplicate IDs."""

        return len(self.tool_calls)

    @property
    def tool_call_count(self) -> int:
        """Alias used by the maintenance trigger threshold."""

        return self.started_event_count


@dataclass
class _MutableToolCallTrace:
    sequence_index: int
    step: int
    tool_call: ToolCall
    result: ToolExecutionResult | None = None


@dataclass
class _MutableTurnRecord:
    correlation_id: str
    prompt: str
    input_images: tuple[ImagePart, ...]
    matched_skills: tuple[SkillDefinition, ...] = ()
    tool_calls: list[_MutableToolCallTrace] = field(default_factory=list)

    def snapshot(self) -> TurnRecord:
        traces = tuple(
            ToolCallTrace(
                sequence_index=trace.sequence_index,
                step=trace.step,
                tool_call=trace.tool_call,
                result=trace.result,
            )
            for trace in self.tool_calls
        )
        # Compatibility lookup for callers that only know the call ID. When an
        # invalid producer reuses an ID, the most recently completed trace wins;
        # the full ordered results remain available on ``tool_calls``.
        latest_results = {
            trace.tool_call.id: trace.result
            for trace in traces
            if trace.result is not None
        }
        return TurnRecord(
            correlation_id=self.correlation_id,
            prompt=self.prompt,
            input_images=self.input_images,
            matched_skills=self.matched_skills,
            tool_calls=traces,
            results_by_call_id=latest_results,
        )


class SkillTurnState:
    """Track active turns until an Agent terminal event consumes them."""

    def __init__(self) -> None:
        self._turns: dict[str, _MutableTurnRecord] = {}

    def start(self, event: UserInputEvent) -> bool:
        """Start a turn, replacing stale state for a duplicate correlation ID.

        Returns ``True`` for a new correlation ID and ``False`` when no ID was
        supplied or an existing record had to be replaced. The input is copied
        immediately so later mutation of the source event cannot alter state.
        """

        correlation_id = event.correlation_id
        if not _has_correlation_id(correlation_id):
            return False
        is_new = correlation_id not in self._turns
        self._turns[correlation_id] = _MutableTurnRecord(
            correlation_id=correlation_id,
            prompt=event.prompt,
            input_images=tuple(
                ImagePart(url=image.url, media_type=image.media_type)
                for image in event.input_images
            ),
        )
        return is_new

    def set_matched_skills(
        self,
        correlation_id: str | None,
        skills: Iterable[SkillDefinition],
    ) -> bool:
        """Save a defensive snapshot of Skills matched during this turn."""

        turn = self._get(correlation_id)
        if turn is None:
            return False
        turn.matched_skills = tuple(_snapshot_skill(skill) for skill in skills)
        return True

    def record_tool_started(self, event: AgentToolStartedEvent) -> bool:
        """Append every started event in arrival order, including duplicate IDs."""

        turn = self._get(event.correlation_id)
        if turn is None:
            return False
        turn.tool_calls.append(
            _MutableToolCallTrace(
                sequence_index=len(turn.tool_calls),
                step=event.step,
                tool_call=_snapshot_tool_call(event.tool_call),
            )
        )
        return True

    def record_tool_completed(self, event: AgentToolCompletedEvent) -> bool:
        """Fill the earliest unfinished trace with the same call ID.

        Duplicate call IDs are deterministic: each completed event advances to
        the next unfinished trace in started-event order. Extra or unknown
        completed events are ignored.
        """

        turn = self._get(event.correlation_id)
        if turn is None:
            return False
        call_id = event.tool_call.id
        for trace in turn.tool_calls:
            if trace.tool_call.id == call_id and trace.result is None:
                trace.result = _snapshot_result(event.result)
                return True
        return False

    def pop(self, correlation_id: str | None) -> TurnRecord | None:
        """Remove an active turn and return one immutable snapshot."""

        turn = self._pop_mutable(correlation_id)
        return turn.snapshot() if turn is not None else None

    def discard(self, correlation_id: str | None) -> TurnRecord | None:
        """Remove a failed turn, returning its final snapshot when present."""

        return self.pop(correlation_id)

    def _get(
        self,
        correlation_id: str | None,
    ) -> _MutableTurnRecord | None:
        if not _has_correlation_id(correlation_id):
            return None
        return self._turns.get(correlation_id)

    def _pop_mutable(
        self,
        correlation_id: str | None,
    ) -> _MutableTurnRecord | None:
        if not _has_correlation_id(correlation_id):
            return None
        return self._turns.pop(correlation_id, None)


def _has_correlation_id(correlation_id: str | None) -> bool:
    return bool(correlation_id and correlation_id.strip())


def _snapshot_skill(skill: SkillDefinition) -> SkillDefinition:
    return SkillDefinition(
        name=skill.name,
        description=skill.description,
        path=skill.path,
        scope=skill.scope,
        metadata=_freeze_value(dict(skill.metadata)),
    )


def _snapshot_tool_call(tool_call: ToolCall) -> ToolCall:
    arguments = _freeze_value(tool_call.arguments)
    if not isinstance(arguments, Mapping):
        raise TypeError("ToolCall.arguments must be a mapping")
    return ToolCall(
        id=tool_call.id,
        name=tool_call.name,
        arguments=arguments,  # type: ignore[arg-type]
    )


def _snapshot_result(result: ToolExecutionResult) -> ToolExecutionResult:
    return ToolExecutionResult(
        success=result.success,
        output=_freeze_value(result.output),
        error=result.error,
    )


def _freeze_value(value: Any) -> Any:
    """Convert arbitrary trace data into detached immutable JSON-like data."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, enum.Enum):
        return _freeze_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return MappingProxyType({"type": "bytes", "size": len(value)})
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen = [_freeze_value(item) for item in value]
        return tuple(sorted(frozen, key=repr))
    if is_dataclass(value) and not isinstance(value, type):
        return MappingProxyType(
            {
                item.name: _freeze_value(getattr(value, item.name))
                for item in fields(value)
            }
        )
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _freeze_value(model_dump(mode="json"))
    return str(value)
