"""Focused tests for RuntimeUpdate consumption without Textual."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.domain.chat_state import ChatState, RuntimePhase
from apps.tui.src.event_pipeline import (
    CompleteAssistantMessage,
    FinishTurn,
    SetRuntimeStatus,
    UiAction,
)
from apps.tui.src.event_pipeline.dispatcher import ProjectorRegistry
from apps.tui.src.services.update_stream import UpdateFatal, UpdateStream


class EmptyProjector:
    def project(self, event: RuntimeUpdateModel, *, historical: bool = False):
        del event, historical
        return ()


class AssistantProjector:
    def project(self, event: RuntimeUpdateModel, *, historical: bool = False):
        del historical
        return (
            CompleteAssistantMessage(
                task_id=str(event.task_id),
                text=str(event.payload.get("text", "")),
            ),
        )


class StatusProjector:
    def project(self, event: RuntimeUpdateModel, *, historical: bool = False):
        del historical
        return (
            SetRuntimeStatus(
                task_id=str(event.task_id),
                status="running",
                text="Running",
            ),
        )


class FinishProjector:
    def project(self, event: RuntimeUpdateModel, *, historical: bool = False):
        del historical
        return (
            FinishTurn(
                task_id=str(event.task_id),
                status=str(event.payload.get("status", "completed")),
            ),
        )


class FakeClient:
    session_id = "session-1"
    workspace_key = "workspace-1"

    def __init__(self, status: dict[str, Any] | None = None) -> None:
        self.status = status or {"lifecycle": "completed", "run_id": None}

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        del task_id
        return self.status


def update(
    update_type: str,
    *,
    sequence: int | None = None,
    task_id: str | None = "task-1",
    session_id: str = "session-1",
    workspace_key: str = "workspace-1",
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> RuntimeUpdateModel:
    return RuntimeUpdateModel(
        workspace_key=workspace_key,
        session_id=session_id,
        task_id=task_id,
        type=update_type,
        payload=payload or {},
        occurred_at=occurred_at or datetime.now(UTC),
        sequence=sequence,
    )


def make_stream(
    *,
    chat_state: ChatState | None = None,
    projectors: ProjectorRegistry | None = None,
) -> tuple[UpdateStream, list[tuple[UiAction, ...]], list[tuple[str, BaseException]], list[None]]:
    state = chat_state or ChatState()
    registry = projectors or ProjectorRegistry()
    actions: list[tuple[UiAction, ...]] = []
    fatal: list[tuple[str, BaseException]] = []
    user_input: list[None] = []

    async def on_actions(batch: tuple[UiAction, ...]) -> None:
        actions.append(batch)

    def on_fatal(stage: str, cause: BaseException) -> None:
        fatal.append((stage, cause))

    def on_user_input() -> None:
        user_input.append(None)

    stream = UpdateStream(
        registry,
        state,
        session_scope=lambda: ("session-1", "workspace-1"),
        on_actions=on_actions,
        on_fatal=on_fatal,
        on_user_input=on_user_input,
    )
    return stream, actions, fatal, user_input


def test_sequence_gap_raises_update_fatal() -> None:
    registry = ProjectorRegistry()
    registry.register("task.started", EmptyProjector())
    stream, _, _, _ = make_stream(projectors=registry)

    asyncio.run(stream.project(update("task.started", sequence=1)))
    with pytest.raises(UpdateFatal) as error:
        asyncio.run(stream.project(update("task.started", sequence=3)))

    assert error.value.stage == "RuntimeUpdate sequence"


def test_early_updates_replay_in_sequence_order() -> None:
    stream, _, _, _ = make_stream()
    stream.buffer(update("task.started", sequence=2))
    stream.buffer(update("task.started", sequence=1))

    assert [item.sequence for item in stream.take_early()] == [1, 2]
    assert stream.take_early() == ()


def test_dispatch_in_progress_buffers_updates_until_task_is_known() -> None:
    state = ChatState()
    state.phase = RuntimePhase.READY
    state.enqueue("queued")
    state.begin_dispatch(1)
    stream, actions, _, _ = make_stream(chat_state=state)
    early = update("user.message", payload={"text": "queued"}, sequence=1)

    assert asyncio.run(stream.project(early)) == ()
    assert actions == []
    assert stream.take_early() == (early,)


def test_late_delta_after_assistant_message_is_ignored() -> None:
    registry = ProjectorRegistry()
    registry.register("assistant.message", AssistantProjector())
    registry.register("assistant.text_delta", AssistantProjector())
    stream, actions, _, _ = make_stream(projectors=registry)

    asyncio.run(
        stream.project(
            update(
                "assistant.message",
                sequence=1,
                payload={"step": 1, "text": "complete"},
            )
        )
    )
    asyncio.run(
        stream.project(
            update(
                "assistant.text_delta",
                sequence=2,
                payload={"step": 1, "text": "late"},
            )
        )
    )

    assert len(actions) == 1
    assert stream.last_sequence == 1


def test_inactive_session_is_ignored() -> None:
    stream, actions, _, _ = make_stream()

    result = asyncio.run(
        stream.project(
            update(
                "user.message",
                session_id="other-session",
                payload={"text": "ignored"},
            )
        )
    )

    assert result == ()
    assert actions == []


def test_historical_runtime_status_is_filtered() -> None:
    registry = ProjectorRegistry()
    registry.register("task.started", StatusProjector())
    stream, actions, _, _ = make_stream(projectors=registry)

    result = asyncio.run(
        stream.project(update("task.started", sequence=1), historical=True)
    )

    assert result == ()
    assert actions == []


def test_user_input_notifies_owner() -> None:
    stream, _, _, user_input = make_stream()

    asyncio.run(
        stream.project(update("user.message", payload={"text": "hello"}))
    )

    assert user_input == [None]


def test_reconcile_completed_task_projects_finish_turn() -> None:
    registry = ProjectorRegistry()
    registry.register("task.finished", FinishProjector())
    state = ChatState(phase=RuntimePhase.RUNNING, active_task_id="task-1")
    stream, actions, _, _ = make_stream(chat_state=state, projectors=registry)

    result = asyncio.run(stream.reconcile(FakeClient(), "task-1"))

    assert result == (FinishTurn(task_id="task-1", status="completed"),)
    assert actions == [result]


def test_reset_replaces_sequence_and_early_buffer() -> None:
    stream, _, _, _ = make_stream()
    stream.buffer(update("task.started", sequence=2))
    stream.last_sequence = 5

    stream.reset(last_sequence=3)

    assert stream.last_sequence == 3
    assert stream.take_early() == ()
