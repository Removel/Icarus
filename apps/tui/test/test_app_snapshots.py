"""Visual regression coverage for the complete Icarus Textual shell."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    RuntimeUpdateModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
)
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.chat_state import RuntimePhase
from apps.tui.src.event_pipeline import AppendToolStarted, UpdateToolCompleted
from apps.tui.src.screens import SessionPicker
from apps.tui.src.widgets import (
    AssistantMessage,
    ConversationView,
    ErrorMessage,
    PersistentComposer,
    QueuePanel,
    ToolMessage,
    TurnStatusMessage,
    UserMessage,
    WelcomeMessage,
)


WORKSPACE = "/workspace/icarus-demo"


class SnapshotSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        self.closed = False

    async def next_update(self):
        item = await self.queue.get()
        if item is None:
            raise RuntimeError("snapshot subscription is closed")
        return item

    def publish(self, source_plugin_id: str, event: object) -> None:
        del source_plugin_id
        if not self.closed:
            if not isinstance(event, RuntimeUpdateModel):
                raise TypeError("SnapshotSubscription accepts RuntimeUpdateModel")
            self.queue.put_nowait(event)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.put_nowait(None)


class SnapshotService:
    session_id = "snapshot-session"
    workspace_key = "workspace"

    def __init__(self) -> None:
        self.subscription = SnapshotSubscription()
        self.submissions: list[str] = []
        self.submission_images: list[tuple[Path, ...]] = []
        self.started = False
        self.session_summaries = ()

    async def start(self) -> None:
        self.started = True

    def subscribe_updates(self) -> SnapshotSubscription:
        if not self.started:
            raise RuntimeError("snapshot service is not running")
        return self.subscription

    async def get_session_history(self, *, after_sequence=0):
        return SessionHistoryModel(records=(), history_cursor=after_sequence)

    async def list_sessions(self):
        return self.session_summaries

    async def get_session_status(self):
        return {
            "workspace_key": self.workspace_key,
            "session_id": self.session_id,
            "lifecycle": "ready",
        }

    async def discard_empty_session(self, session_id):
        return DiscardEmptySessionResultModel(
            workspace_key=self.workspace_key,
            session_id=session_id,
            status="discarded",
        )

    async def submit(
        self,
        prompt: str,
        *,
        submission_id: str,
        resources=(),
        display_text=None,
    ) -> SubmitAccepted:
        del submission_id
        task_id = f"task-{len(self.submissions) + 1}"
        self.submissions.append(prompt)
        self.submission_images.append(tuple(resources))
        self.subscription.queue.put_nowait(
            RuntimeUpdateModel(
                workspace_key="workspace",
                session_id=self.session_id,
                task_id=task_id,
                type="user.message",
                payload={
                    "text": prompt if display_text is None else display_text,
                    "resources": [],
                },
                occurred_at=datetime.now(UTC),
            )
        )
        await asyncio.sleep(0)
        return SubmitAccepted(task_id=task_id, queue_position=0)

    async def close(self) -> None:
        self.started = False
        self.subscription.close()

    async def cancel_task(self, task_id: str, reason: str | None = None):
        del reason
        return TaskOperationResult(task_id=task_id, status="accepted")

    async def get_task_status(self, task_id):
        return {"task_id": task_id, "lifecycle": "running"}

    async def reconnect(self):
        return self.subscription


class BlockingSnapshotService(SnapshotService):
    def __init__(self) -> None:
        super().__init__()
        self.start_entered = asyncio.Event()
        self.start_release = asyncio.Event()

    async def start(self) -> None:
        self.start_entered.set()
        await self.start_release.wait()
        await super().start()


def make_app(service: SnapshotService | None = None) -> IcarusTextualApp:
    service = service or SnapshotService()

    async def runtime_factory(session_id, create_if_missing):
        del session_id, create_if_missing
        return service

    return IcarusTextualApp(
        runtime_factory=runtime_factory,
        workspace_path=WORKSPACE,
    )


async def wait_until(pilot, predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await pilot.pause()
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


async def wait_ready(pilot) -> None:
    conversation = pilot.app.query_one(ConversationView)
    await wait_until(
        pilot,
        lambda: (
            pilot.app.chat_state.phase == RuntimePhase.READY
            and conversation.is_mounted
            and len(conversation.query(WelcomeMessage)) == 1
        ),
    )


async def submit_text(pilot, text: str) -> None:
    keys = ["ctrl+j" if character == "\n" else character for character in text]
    await pilot.press(*keys, "enter")
    await wait_until(
        pilot,
        lambda: (
            bool(pilot.app.service and pilot.app.service.submissions)
            and len(pilot.app.query(UserMessage))
            == len(pilot.app.service.submissions)
        ),
    )


def publish(pilot, source_plugin_id: str, event: object) -> None:
    assert pilot.app.service is not None
    pilot.app.service.subscription.publish(source_plugin_id, event)


def input_started(task_id: str = "task-1") -> RuntimeUpdateModel:
    return runtime_update("task.started", task_id=task_id)


def runtime_update(
    update_type, *, task_id="task-1", payload=None
) -> RuntimeUpdateModel:
    return RuntimeUpdateModel(
        workspace_key="workspace", session_id="snapshot-session",
        task_id=task_id, type=update_type, payload=payload or {},
        occurred_at=datetime.now(UTC),
    )


def test_snapshot_initial_welcome(snap_compare):
    service = SnapshotService()

    async def prepare(pilot) -> None:
        await wait_ready(pilot)

    assert snap_compare(
        make_app(service),
        terminal_size=(100, 30),
        run_before=prepare,
    )


def test_snapshot_session_picker(snap_compare):
    service = SnapshotService()
    service.session_summaries = (
        SessionSummaryModel(
            session_id="current-session-12345678",
            first_user_input="Implement Session switching for the TUI",
        ),
        SessionSummaryModel(
            session_id="older-session-87654321",
            first_user_input=(
                "Review a long first message that should fit the available row"
            ),
        ),
    )
    service.session_id = "current-session-12345678"

    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await pilot.press(*"/resume", "enter")
        await wait_until(pilot, lambda: isinstance(pilot.app.screen, SessionPicker))

    assert snap_compare(
        make_app(service),
        terminal_size=(80, 24),
        run_before=prepare,
    )


def test_snapshot_empty_session_picker(snap_compare):
    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await pilot.press(*"/resume", "enter")
        await wait_until(pilot, lambda: isinstance(pilot.app.screen, SessionPicker))

    assert snap_compare(
        make_app(),
        terminal_size=(58, 18),
        run_before=prepare,
    )


def test_snapshot_initializing_with_multiline_queue(snap_compare):
    service = BlockingSnapshotService()

    async def prepare(pilot) -> None:
        await service.start_entered.wait()
        await pilot.press(*"Wait for the runtime", "enter")
        await pilot.press(
            *"line 1",
            "ctrl+j",
            *"line 2",
            "ctrl+j",
            *"line 3",
            "enter",
        )
        await pilot.press(
            *"draft 1",
            "ctrl+j",
            *"draft 2",
            "ctrl+j",
            *"draft 3",
            "ctrl+j",
            *"draft 4",
            "ctrl+j",
            *"draft 5",
            "ctrl+j",
            *"draft 6",
            "ctrl+j",
            *"draft 7",
            "ctrl+j",
            *"draft 8",
        )
        await wait_until(
            pilot,
            lambda: pilot.app.query_one(QueuePanel).items
            == ("Wait for the runtime", "line 1\nline 2\nline 3"),
        )

    assert snap_compare(
        make_app(service),
        terminal_size=(58, 24),
        run_before=prepare,
    )


def test_snapshot_short_initializing_with_queue(snap_compare):
    service = BlockingSnapshotService()

    async def prepare(pilot) -> None:
        await service.start_entered.wait()
        await pilot.press(*"queued first", "enter")
        await pilot.press(*"queued second", "enter")
        await pilot.press(
            *"draft 1",
            "ctrl+j",
            *"draft 2",
            "ctrl+j",
            *"draft 3",
            "ctrl+j",
            *"draft 4",
            "ctrl+j",
            *"draft 5",
            "ctrl+j",
            *"draft 6",
            "ctrl+j",
            *"draft 7",
            "ctrl+j",
            *"draft 8",
        )
        await wait_until(
            pilot,
            lambda: pilot.app.query_one(QueuePanel).items
            == ("queued first", "queued second"),
        )

    assert snap_compare(
        make_app(service),
        terminal_size=(58, 12),
        run_before=prepare,
    )


def test_snapshot_image_markers_in_queue_and_draft(snap_compare, tmp_path):
    service = BlockingSnapshotService()

    async def prepare(pilot) -> None:
        await service.start_entered.wait()
        composer = pilot.app.query_one(PersistentComposer)
        composer.load_text("比较这张图 ")
        composer.move_cursor(composer.document.end)
        composer.attach_image(tmp_path / "first.png")
        await pilot.press("enter")
        composer.load_text("继续参考 ")
        composer.move_cursor(composer.document.end)
        composer.attach_image(tmp_path / "second.png")
        await wait_until(
            pilot,
            lambda: pilot.app.query_one(QueuePanel).items
            == ("比较这张图 [#image1]",),
        )

    assert snap_compare(
        make_app(service),
        terminal_size=(80, 24),
        run_before=prepare,
    )


def test_snapshot_streaming_markdown_with_draft(snap_compare):
    markdown = (
        "# Implementation update\n\n"
        "The runtime now streams **Markdown** while tools run.\n\n"
        "- Composer stays editable\n"
        "- Queue remains local\n\n"
        "```python\nawait service.submit(prompt)\n```"
    )

    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Build the persistent Textual interface")
        publish(pilot, "user-input", input_started())
        publish(
            pilot,
            "agent",
            runtime_update(
                "assistant.text_delta",
                task_id="task-1",
                payload={"step": 1, "text": markdown},
            ),
        )
        await pilot.press(*"Add a focused regression test next")
        await wait_until(
            pilot,
            lambda: len(pilot.app.query(AssistantMessage)) == 1
            and pilot.app.query_one(AssistantMessage).markdown_text == markdown,
        )

    assert snap_compare(
        make_app(),
        terminal_size=(100, 34),
        run_before=prepare,
    )


def test_snapshot_running_with_pending_queue(snap_compare):
    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Implement the Runtime event projection")
        publish(pilot, "user-input", input_started())
        publish(
            pilot,
            "agent",
            runtime_update(
                "assistant.text_delta",
                task_id="task-1",
                payload={
                    "step": 1,
                    "text": (
                        "The projection layer is in place. I am validating the "
                        "full interaction flow now."
                    ),
                },
            ),
        )
        await pilot.press(*"Review the documentation", "enter")
        await pilot.press(
            *"Run the complete suite",
            "ctrl+j",
            *"and inspect the snapshots",
            "enter",
        )
        await wait_until(
            pilot,
            lambda: pilot.app.query_one(QueuePanel).items
            == (
                "Review the documentation",
                "Run the complete suite\nand inspect the snapshots",
            ),
        )

    assert snap_compare(
        make_app(),
        terminal_size=(100, 32),
        run_before=prepare,
    )


def test_snapshot_tool_success_uses_positive_state_color(snap_compare):
    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Inspect the workspace configuration")
        conversation = pilot.app.query_one(ConversationView)
        await conversation.apply_action(
            AppendToolStarted(
                task_id="task-1",
                call_id="call-read",
                tool_name="read_workspace_config",
                arguments_json='{"path":"settings.json"}',
            )
        )
        await conversation.apply_action(
            UpdateToolCompleted(
                task_id="task-1",
                call_id="call-read",
                tool_name="read_workspace_config",
                success=True,
            )
        )
        await wait_until(
            pilot,
            lambda: pilot.app.query_one(ToolMessage).success is True,
        )

    assert snap_compare(
        make_app(),
        terminal_size=(100, 32),
        run_before=prepare,
    )


def test_snapshot_tool_failure_and_agent_error(snap_compare):
    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Inspect the workspace configuration")
        publish(pilot, "user-input", input_started())
        publish(
            pilot,
            "agent",
            runtime_update(
                "assistant.text_delta",
                task_id="task-1",
                payload={
                    "step": 1,
                    "text": "I will read the workspace configuration first.",
                },
            ),
        )
        publish(
            pilot,
            "agent",
            runtime_update(
                "tool.started",
                task_id="task-1",
                payload={
                    "step": 1,
                    "call_id": "call-settings",
                    "tool_name": "read_workspace_config",
                    "arguments": {"path": "settings.json"},
                },
            ),
        )
        publish(
            pilot,
            "agent",
            runtime_update(
                "tool.completed",
                task_id="task-1",
                payload={
                    "step": 1,
                    "call_id": "call-settings",
                    "tool_name": "read_workspace_config",
                    "success": False,
                    "error": "permission denied",
                },
            ),
        )
        publish(
            pilot,
            "agent",
            runtime_update(
                "task.error",
                task_id="task-1",
                payload={
                    "fatal": True,
                    "code": "agent_run_failed",
                    "step": 1,
                    "error_type": "ToolExecutionError",
                    "message": "Could not read settings.json",
                    "run_id": None,
                },
            ),
        )
        publish(
            pilot,
            "user-input",
            runtime_update(
                "task.finished",
                task_id="task-1",
                payload={"status": "failed", "run_id": None},
            ),
        )
        await wait_until(
            pilot,
            lambda: len(pilot.app.query(ErrorMessage)) == 1
            and len(pilot.app.query(TurnStatusMessage)) == 1
            and pilot.app.query_one(ToolMessage).success is False,
        )

    assert snap_compare(
        make_app(),
        terminal_size=(100, 32),
        run_before=prepare,
    )


def test_snapshot_narrow_running_layout(snap_compare):
    expected_markdown = "The conversation remains readable on a narrow terminal."

    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Check the narrow terminal layout")
        publish(pilot, "user-input", input_started())
        publish(
            pilot,
            "agent",
            runtime_update(
                "assistant.text_delta",
                task_id="task-1",
                payload={"step": 1, "text": expected_markdown},
            ),
        )
        await pilot.press(*"Queue this follow-up", "enter")
        await pilot.press(*"Editable draft")
        await wait_until(
            pilot,
            lambda: (
                pilot.app.query_one(QueuePanel).items
                == ("Queue this follow-up",)
                and len(pilot.app.query(AssistantMessage)) == 1
                and pilot.app.query_one(AssistantMessage).markdown_text
                == expected_markdown
            ),
        )

    assert snap_compare(
        make_app(),
        terminal_size=(58, 24),
        run_before=prepare,
    )
