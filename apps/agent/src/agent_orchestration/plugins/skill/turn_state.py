"""Per-turn Skill matches and ordered Agent tool execution traces."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
import enum
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import (
    ImagePart,
    Message,
    TextPart,
    ToolCall,
)


class ToolTrajectoryError(ValueError):
    """Raised when a completed Agent response has an invalid tool trajectory."""


@dataclass(frozen=True)
class ToolCallTrace:
    """One tool call in completed Agent message order."""

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
    """Deeply immutable snapshot of one completed Agent turn."""

    task_id: str
    prompt: str
    input_images: tuple[ImagePart, ...]
    matched_skills: tuple[SkillDefinition, ...]
    tool_calls: tuple[ToolCallTrace, ...]

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

    @property
    def tool_call_count(self) -> int:
        """Alias used by the maintenance trigger threshold."""

        return len(self.tool_calls)


@dataclass
class _MutableToolCallTrace:
    sequence_index: int
    step: int
    tool_call: ToolCall
    result: ToolExecutionResult | None = None


@dataclass
class _MutableTurnRecord:
    task_id: str
    prompt: str
    input_images: tuple[ImagePart, ...]
    matched_skills: tuple[SkillDefinition, ...] = ()


class SkillTurnState:
    """Track active turns until an Agent terminal event consumes them."""

    def __init__(self) -> None:
        self._turns: dict[str, _MutableTurnRecord] = {}

    def start(self, event: UserInputEvent) -> bool:
        """Start a turn, replacing stale state for a duplicate task ID.

        Returns ``True`` for a new task ID and ``False`` when no ID was
        supplied or an existing record had to be replaced. The input is copied
        immediately so later mutation of the source event cannot alter state.
        """

        task_id = event.task_id
        if not _has_task_id(task_id):
            return False
        is_new = task_id not in self._turns
        self._turns[task_id] = _MutableTurnRecord(
            task_id=task_id,
            prompt=event.prompt,
            input_images=tuple(
                ImagePart(url=image.url, media_type=image.media_type)
                for image in event.input_images
            ),
        )
        return is_new

    def set_matched_skills(
        self,
        task_id: str | None,
        skills: Iterable[SkillDefinition],
    ) -> bool:
        """Save a defensive snapshot of Skills matched during this turn."""

        turn = self._get(task_id)
        if turn is None:
            return False
        turn.matched_skills = tuple(_snapshot_skill(skill) for skill in skills)
        return True

    def pop_completed(
        self,
        task_id: str | None,
        messages: Sequence[Message],
    ) -> TurnRecord | None:
        """Pop a turn and rebuild its complete tool trajectory from messages."""

        return self.pop_with_tool_traces(
            task_id,
            tool_traces_from_messages(messages),
        )

    def pop_with_tool_traces(
        self,
        task_id: str | None,
        tool_calls: Sequence[ToolCallTrace],
    ) -> TurnRecord | None:
        """Pop a turn using an already validated immutable trajectory."""

        turn = self._pop_mutable(task_id)
        if turn is None:
            return None
        return TurnRecord(
            task_id=turn.task_id,
            prompt=turn.prompt,
            input_images=turn.input_images,
            matched_skills=turn.matched_skills,
            tool_calls=tuple(tool_calls),
        )

    def discard(self, task_id: str | None) -> bool:
        """Remove a failed or non-maintained turn when present."""

        return self._pop_mutable(task_id) is not None

    def _get(
        self,
        task_id: str | None,
    ) -> _MutableTurnRecord | None:
        if not _has_task_id(task_id):
            return None
        return self._turns.get(task_id)

    def _pop_mutable(
        self,
        task_id: str | None,
    ) -> _MutableTurnRecord | None:
        if not _has_task_id(task_id):
            return None
        return self._turns.pop(task_id, None)


def _has_task_id(task_id: str | None) -> bool:
    return bool(task_id and task_id.strip())


def tool_traces_from_messages(
    messages: Sequence[Message],
) -> tuple[ToolCallTrace, ...]:
    """Build ordered current-turn traces from a completed Agent message list."""

    current_turn = _current_turn_messages(messages)

    mutable: list[_MutableToolCallTrace] = []
    step = 0
    for message in current_turn:
        if message.role == "assistant":
            step += 1
            for tool_call in message.tool_calls:
                mutable.append(
                    _MutableToolCallTrace(
                        sequence_index=len(mutable),
                        step=step,
                        tool_call=_snapshot_tool_call(tool_call),
                    )
                )
            continue
        if message.role != "tool":
            continue
        call_id = message.tool_call_id
        if not call_id:
            raise ToolTrajectoryError("tool result message is missing tool_call_id")
        target = next(
            (
                trace
                for trace in mutable
                if trace.tool_call.id == call_id and trace.result is None
            ),
            None,
        )
        if target is None:
            raise ToolTrajectoryError(
                f"tool result has no unfinished ToolCall: {call_id}"
            )
        target.result = _parse_tool_result_message(message)

    unfinished = [
        trace.tool_call.id for trace in mutable if trace.result is None
    ]
    if unfinished:
        raise ToolTrajectoryError(
            "ToolCall results are incomplete: " + ", ".join(unfinished)
        )
    return tuple(
        ToolCallTrace(
            sequence_index=trace.sequence_index,
            step=trace.step,
            tool_call=trace.tool_call,
            result=trace.result,
        )
        for trace in mutable
    )


def tool_call_count_from_messages(messages: Sequence[Message]) -> int:
    """Count current-turn ToolCalls without parsing potentially large results."""

    return sum(
        len(message.tool_calls)
        for message in _current_turn_messages(messages)
        if message.role == "assistant"
    )


def _current_turn_messages(messages: Sequence[Message]) -> Sequence[Message]:
    user_indexes = [
        index for index, message in enumerate(messages) if message.role == "user"
    ]
    if not user_indexes:
        raise ToolTrajectoryError("completed Agent messages contain no user message")
    return messages[user_indexes[-1] + 1 :]


def _parse_tool_result_message(message: Message) -> ToolExecutionResult:
    if not message.content or not all(
        isinstance(part, TextPart) for part in message.content
    ):
        raise ToolTrajectoryError(
            f"tool result is not complete text: {message.tool_call_id}"
        )
    raw = "".join(part.text for part in message.content)
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ToolTrajectoryError(
            f"tool result is not valid JSON: {message.tool_call_id}"
        ) from error
    if not isinstance(payload, dict) or not isinstance(
        payload.get("success"), bool
    ):
        raise ToolTrajectoryError(
            f"tool result does not match ToolExecutionResult: {message.tool_call_id}"
        )
    error_message = payload.get("error")
    if error_message is not None and not isinstance(error_message, str):
        raise ToolTrajectoryError(
            f"tool result error must be text: {message.tool_call_id}"
        )
    return ToolExecutionResult(
        success=payload["success"],
        output=payload.get("output"),
        error=error_message,
    )


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
