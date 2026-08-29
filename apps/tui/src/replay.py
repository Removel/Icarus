"""Deterministic RuntimeUpdate JSONL replay for TUI development."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
    UpdateSubscription,
)


SCHEMA_VERSION = 4


class ReplayFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ReplayTurn:
    task_id: str
    updates: tuple[RuntimeUpdateModel, ...]


@dataclass(frozen=True)
class ReplayScenario:
    turns: tuple[ReplayTurn, ...]

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(turn.task_id for turn in self.turns)


def load_replay(path: str | Path) -> ReplayScenario:
    records = []
    with Path(path).open(encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
                records.append(decode_replay_record(value))
            except (json.JSONDecodeError, ReplayFormatError) as error:
                message = (
                    error.msg if isinstance(error, json.JSONDecodeError) else str(error)
                )
                raise ReplayFormatError(
                    f"Line {line_number}: {message}"
                ) from error
    return build_replay_scenario(records)


def decode_replay_record(value: object) -> RuntimeUpdateModel:
    if not isinstance(value, dict):
        raise ReplayFormatError("record must be an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ReplayFormatError(
            f"unsupported schema_version: {value.get('schema_version')}"
        )
    update = dict(value)
    update.pop("schema_version", None)
    try:
        return RuntimeUpdateModel.model_validate(update)
    except ValidationError as error:
        raise ReplayFormatError("invalid RuntimeUpdate") from error


def build_replay_scenario(
    updates: Iterable[RuntimeUpdateModel],
) -> ReplayScenario:
    turns = []
    active_task_id = None
    active = []
    for update in updates:
        if active_task_id is None:
            if update.type != "task.accepted" or update.task_id is None:
                raise ReplayFormatError(
                    "each replay turn must start with task.accepted"
                )
            active_task_id = update.task_id
            active = [update]
            continue
        active.append(update)
        if update.type == "task.finished" and update.task_id == active_task_id:
            turns.append(ReplayTurn(active_task_id, tuple(active)))
            active_task_id = None
            active = []
    if active_task_id is not None:
        raise ReplayFormatError("replay turn has no matching task.finished")
    if not turns:
        raise ReplayFormatError("replay fixture contains no complete turns")
    return ReplayScenario(tuple(turns))


class ReplayRuntimeService:
    """No-model client matching the GatewayClient shape."""

    def __init__(
        self, scenario: ReplayScenario, *, events_per_second: float = 0,
        session_id: str = "replay-session",
    ) -> None:
        if events_per_second < 0:
            raise ValueError("events_per_second cannot be negative")
        self.scenario = scenario
        self.events_per_second = events_per_second
        self.session_id = session_id
        self._subscription = UpdateSubscription()
        self._emit_tasks = set()
        self._next_turn = 0
        self._started = False
        self.submissions = []
        self._task_statuses = {}

    async def start(self) -> None:
        self._started = True

    def subscribe_updates(self) -> UpdateSubscription:
        if not self._started:
            raise RuntimeError("Replay client is not running")
        return self._subscription

    async def submit(
        self, prompt: str, *, submission_id: str, resources=()
    ) -> SubmitAccepted:
        del submission_id, resources
        if self._next_turn >= len(self.scenario.turns):
            raise RuntimeError("Replay scenario has no remaining turns")
        turn = self.scenario.turns[self._next_turn]
        self._next_turn += 1
        self.submissions.append(prompt)
        self._task_statuses[turn.task_id] = {
            "task_id": turn.task_id, "lifecycle": "running"
        }
        first = turn.updates[0]
        self._subscription.publish(first)
        await asyncio.sleep(0)
        task = asyncio.create_task(self._emit(turn.updates[1:]))
        self._emit_tasks.add(task)
        task.add_done_callback(self._emit_tasks.discard)
        return SubmitAccepted(turn.task_id, int(first.payload["queue_position"]))

    async def cancel_task(self, task_id, reason=None):
        del reason
        return TaskOperationResult(task_id, "accepted")

    async def get_task_status(self, task_id):
        return self._task_statuses.get(
            task_id, {"task_id": task_id, "lifecycle": "running"}
        )

    async def reconnect(self):
        return self._subscription

    async def close(self) -> None:
        for task in tuple(self._emit_tasks):
            task.cancel()
        if self._emit_tasks:
            await asyncio.gather(*self._emit_tasks, return_exceptions=True)
        self._subscription.close()
        self._started = False

    async def _emit(self, updates) -> None:
        delay = 1 / self.events_per_second if self.events_per_second else 0
        for update in updates:
            await asyncio.sleep(delay)
            self._subscription.publish(update)
            if update.type == "task.finished" and update.task_id is not None:
                self._task_statuses[update.task_id] = {
                    "task_id": update.task_id,
                    "lifecycle": update.payload["status"],
                    "run_id": update.payload.get("run_id"),
                }
