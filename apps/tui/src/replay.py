"""Deterministic JSONL replay support for Icarus TUI development."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins import (
    InputAccepted,
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.application.output_bridge import OutputEvent
from apps.agent.src.model_provider.types import Message, TextPart, ToolCall


SCHEMA_VERSION = 2


class ReplayFormatError(ValueError):
    """A JSONL fixture does not match the supported replay schema."""


@dataclass(frozen=True)
class ReplayTurn:
    task_id: str
    events: tuple[OutputEvent, ...]


@dataclass(frozen=True)
class ReplayScenario:
    turns: tuple[ReplayTurn, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(turn.task_id for turn in self.turns)


def load_replay(path: str | Path) -> ReplayScenario:
    fixture_path = Path(path)
    records: list[OutputEvent] = []
    with fixture_path.open(encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReplayFormatError(
                    f"Line {line_number}: invalid JSON: {error.msg}"
                ) from error
            try:
                records.append(decode_replay_record(value))
            except ReplayFormatError as error:
                raise ReplayFormatError(
                    f"Line {line_number}: {error}"
                ) from error
    return build_replay_scenario(records)


def decode_replay_record(value: object) -> OutputEvent:
    record = _require_dict(value, "record")
    schema_version = _require_int(record, "schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ReplayFormatError(
            f"unsupported schema_version: {schema_version}"
        )

    source = _require_str(record, "source_plugin_id")
    event_type = _require_str(record, "event_type")
    task_id = _require_str(record, "task_id")
    payload = _require_dict(record.get("payload"), "payload")

    decoders = {
        "input_queued": _decode_input_queued,
        "input_started": _decode_input_started,
        "user_input": _decode_user_input,
        "agent_text_delta": _decode_agent_text_delta,
        "agent_tool_started": _decode_agent_tool_started,
        "agent_tool_completed": _decode_agent_tool_completed,
        "agent_error": _decode_agent_error,
        "agent_completed": _decode_agent_completed,
        "input_finished": _decode_input_finished,
    }
    decoder = decoders.get(event_type)
    if decoder is None:
        raise ReplayFormatError(f"unsupported event_type: {event_type}")
    event = decoder(task_id, payload)
    return source, event


def build_replay_scenario(events: Iterable[OutputEvent]) -> ReplayScenario:
    """Split a flat stream into turns while retaining unrelated events."""

    turns: list[ReplayTurn] = []
    active_task_id: str | None = None
    active_events: list[OutputEvent] = []

    for output_event in events:
        source, event = output_event
        if active_task_id is None:
            if not isinstance(event, InputQueuedEvent):
                raise ReplayFormatError(
                    "each replay turn must start with InputQueuedEvent"
                )
            if source != "user-input":
                raise ReplayFormatError(
                    "InputQueuedEvent must come from user-input"
                )
            active_task_id = event.task_id
            active_events = [output_event]
            continue

        active_events.append(output_event)
        if (
            isinstance(event, InputFinishedEvent)
            and source == "user-input"
            and event.task_id == active_task_id
        ):
            turns.append(
                ReplayTurn(
                    task_id=active_task_id,
                    events=tuple(active_events),
                )
            )
            active_task_id = None
            active_events = []

    if active_task_id is not None:
        raise ReplayFormatError(
            f"replay turn has no matching InputFinishedEvent: {active_task_id}"
        )
    if not turns:
        raise ReplayFormatError("replay fixture contains no complete turns")
    return ReplayScenario(turns=tuple(turns))


class ReplaySubscription:
    """An in-memory real-time subscription matching the production shape."""

    def __init__(self, on_close) -> None:
        self._queue: asyncio.Queue[OutputEvent | None] = asyncio.Queue()
        self._on_close = on_close
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def next_event(self) -> OutputEvent:
        if self._closed:
            raise RuntimeError("Replay subscription is closed")
        item = await self._queue.get()
        if item is None:
            raise RuntimeError("Replay subscription is closed")
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._on_close(self)
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(None)

    def publish(self, item: OutputEvent) -> None:
        if not self._closed:
            self._queue.put_nowait(item)


class ReplayRuntimeService:
    """No-model service adapter for the complete Textual shell."""

    def __init__(
        self,
        scenario: ReplayScenario,
        *,
        events_per_second: float = 0,
        session_id: str = "replay-session",
    ) -> None:
        if events_per_second < 0:
            raise ValueError("events_per_second cannot be negative")
        self.scenario = scenario
        self.events_per_second = events_per_second
        self.session_id = session_id
        self._subscriptions: set[ReplaySubscription] = set()
        self._emit_tasks: set[asyncio.Task[None]] = set()
        self._next_turn = 0
        self._started = False
        self._stopped = False
        self.submissions: list[str] = []

    async def start(self) -> None:
        if self._stopped:
            raise RuntimeError("ReplayRuntimeService cannot be restarted")
        self._started = True

    def subscribe_events(self) -> ReplaySubscription:
        if not self._started:
            raise RuntimeError("ReplayRuntimeService is not running")
        subscription = ReplaySubscription(self._remove_subscription)
        self._subscriptions.add(subscription)
        return subscription

    async def submit(
        self,
        prompt: str,
        input_images=None,
    ) -> InputAccepted:
        del input_images
        if not self._started:
            raise RuntimeError("ReplayRuntimeService is not running")
        if self._next_turn >= len(self.scenario.turns):
            raise RuntimeError("Replay scenario has no remaining turns")
        if any(not task.done() for task in self._emit_tasks):
            raise RuntimeError("ReplayRuntimeService already has an active turn")

        turn = self.scenario.turns[self._next_turn]
        self._next_turn += 1
        self.submissions.append(prompt)

        first_source, first_event = turn.events[0]
        assert isinstance(first_event, InputQueuedEvent)
        self._publish((first_source, first_event))
        # Let the output consumer observe the same pre-return race as production.
        await asyncio.sleep(0)

        task = asyncio.create_task(
            self._emit_remaining(turn.events[1:]),
            name=f"tui-replay:{turn.task_id}",
        )
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)
        return InputAccepted(
            task_id=turn.task_id,
            queue_position=first_event.queue_position,
        )

    async def stop(self, timeout: float | None = 30) -> None:
        del timeout
        if self._stopped:
            return
        self._started = False
        self._stopped = True
        for task in tuple(self._emit_tasks):
            task.cancel()
        if self._emit_tasks:
            await asyncio.gather(*tuple(self._emit_tasks), return_exceptions=True)
        self._emit_tasks.clear()
        for subscription in tuple(self._subscriptions):
            subscription.close()

    async def _emit_remaining(
        self,
        events: tuple[OutputEvent, ...],
    ) -> None:
        delay = (
            1 / self.events_per_second
            if self.events_per_second > 0
            else 0
        )
        for item in events:
            await asyncio.sleep(delay)
            self._publish(item)

    def _publish(self, item: OutputEvent) -> None:
        for subscription in tuple(self._subscriptions):
            subscription.publish(item)

    def _remove_subscription(self, subscription: ReplaySubscription) -> None:
        self._subscriptions.discard(subscription)


def _decode_input_queued(task_id: str, payload: dict[str, Any]) -> Event:
    return InputQueuedEvent(
        task_id=task_id,
        queue_position=_require_int(payload, "queue_position"),
    )


def _decode_input_started(task_id: str, payload: dict[str, Any]) -> Event:
    del payload
    return InputStartedEvent(task_id=task_id)


def _decode_user_input(task_id: str, payload: dict[str, Any]) -> Event:
    return UserInputEvent(
        task_id=task_id,
        prompt=_require_str(payload, "prompt"),
    )


def _decode_agent_text_delta(
    task_id: str, payload: dict[str, Any]
) -> Event:
    return AgentTextDeltaEvent(
        task_id=task_id,
        step=_require_int(payload, "step"),
        text=_require_str(payload, "text", allow_empty=True),
    )


def _decode_agent_tool_started(
    task_id: str, payload: dict[str, Any]
) -> Event:
    return AgentToolStartedEvent(
        task_id=task_id,
        step=_require_int(payload, "step"),
        tool_call=_decode_tool_call(payload),
    )


def _decode_agent_tool_completed(
    task_id: str, payload: dict[str, Any]
) -> Event:
    success = _require_bool(payload, "success")
    error = payload.get("error")
    if error is not None and not isinstance(error, str):
        raise ReplayFormatError("payload.error must be a string or null")
    return AgentToolCompletedEvent(
        task_id=task_id,
        step=_require_int(payload, "step"),
        tool_call=_decode_tool_call(payload),
        result=ToolExecutionResult(success=success, error=error),
    )


def _decode_agent_error(task_id: str, payload: dict[str, Any]) -> Event:
    return AgentErrorEvent(
        task_id=task_id,
        step=_require_int(payload, "step"),
        error_type=_require_str(payload, "error_type"),
        error_message=_require_str(payload, "error_message"),
    )


def _decode_agent_completed(
    task_id: str, payload: dict[str, Any]
) -> Event:
    text = _require_str(payload, "text", allow_empty=True)
    message = Message("assistant", [TextPart(text)])
    return AgentCompletedEvent(
        task_id=task_id,
        step=_require_int(payload, "step"),
        response=AgentResponse(message=message, messages=[message]),
    )


def _decode_input_finished(task_id: str, payload: dict[str, Any]) -> Event:
    status = _require_str(payload, "status")
    if status not in {"completed", "failed"}:
        raise ReplayFormatError(
            "payload.status must be completed or failed"
        )
    typed_status: Literal["completed", "failed"] = status  # type: ignore[assignment]
    return InputFinishedEvent(
        task_id=task_id,
        status=typed_status,
    )


def _decode_tool_call(payload: dict[str, Any]) -> ToolCall:
    arguments = _require_dict(payload.get("arguments"), "payload.arguments")
    return ToolCall(
        id=_require_str(payload, "call_id"),
        name=_require_str(payload, "tool_name"),
        arguments=arguments,
    )


def _require_dict(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplayFormatError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ReplayFormatError(f"{name} keys must be strings")
    return value


def _require_str(
    value: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or (not allow_empty and not item):
        suffix = "a string" if allow_empty else "a non-empty string"
        raise ReplayFormatError(f"{key} must be {suffix}")
    return item


def _require_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ReplayFormatError(f"{key} must be an integer")
    return item


def _require_bool(value: dict[str, Any], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ReplayFormatError(f"{key} must be a boolean")
    return item
