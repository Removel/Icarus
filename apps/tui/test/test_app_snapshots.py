"""Visual regression coverage for the complete Icarus Textual shell."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from apps.agent.src.agent_orchestration.capability import (
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.events import TaskErrorEvent
from apps.agent.src.agent_orchestration.plugins import (
    InputAccepted,
    InputFinishedEvent,
    InputStartedEvent,
)
from apps.agent.src.agent_orchestration.run_control import TaskOperationResult
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolCall
from packages.gateway_protocol import RuntimeUpdateModel, SessionHistoryModel
from apps.tui.src.gateway_client.models import SubmitAccepted
from apps.tui.src.app import IcarusTextualApp
from apps.tui.src.chat_state import RuntimePhase
from apps.tui.src.event_pipeline import AppendToolStarted, UpdateToolCompleted
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
        if not self.closed:
            self.queue.put_nowait(_event_update(source_plugin_id, event))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.queue.put_nowait(None)


class SnapshotService:
    session_id = "snapshot-session"

    def __init__(self) -> None:
        self.subscription = SnapshotSubscription()
        self.submissions: list[str] = []
        self.submission_images: list[tuple[Path, ...]] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    def subscribe_updates(self) -> SnapshotSubscription:
        if not self.started:
            raise RuntimeError("snapshot service is not running")
        return self.subscription

    async def get_session_history(self, *, after_sequence=0):
        return SessionHistoryModel(records=(), history_cursor=after_sequence)

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
        return InputAccepted(task_id=task_id, queue_position=0)

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

    async def runtime_factory():
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


def input_started(task_id: str = "task-1") -> InputStartedEvent:
    return InputStartedEvent(task_id=task_id)


def _event_update(source, event) -> RuntimeUpdateModel:
    update_type = source
    payload = {}
    if isinstance(event, InputStartedEvent):
        update_type = "task.started"
    elif isinstance(event, InputFinishedEvent):
        update_type = "task.finished"
        payload = {"status": event.status, "run_id": event.run_id}
    elif isinstance(event, AgentTextDeltaEvent):
        update_type = "assistant.text_delta"
        payload = {"step": event.step, "text": event.text}
    elif isinstance(event, AgentToolStartedEvent):
        update_type = "tool.started"
        payload = {
            "step": event.step, "call_id": event.tool_call.id,
            "tool_name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
    elif isinstance(event, AgentToolCompletedEvent):
        update_type = "tool.completed"
        payload = {
            "step": event.step, "call_id": event.tool_call.id,
            "tool_name": event.tool_call.name,
            "success": event.result.success,
            "error": event.result.error,
        }
    elif isinstance(event, TaskErrorEvent):
        update_type = "task.error"
        payload = {
            "fatal": event.fatal, "code": event.code,
            "error_type": event.error_type, "message": event.error_message,
            "step": event.step, "run_id": event.run_id,
        }
    return RuntimeUpdateModel(
        workspace_key="workspace", session_id="snapshot-session",
        task_id=getattr(event, "task_id", None), type=update_type, payload=payload,
        occurred_at=getattr(event, "occurred_at", datetime.now(UTC)),
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
            AgentTextDeltaEvent(
                task_id="task-1",
                step=1,
                text=markdown,
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
            AgentTextDeltaEvent(
                task_id="task-1",
                step=1,
                text=(
                    "The projection layer is in place. I am validating the "
                    "full interaction flow now."
                ),
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
    tool_call = ToolCall(
        id="call-read",
        name="read_workspace_config",
        arguments={"path": "settings.json"},
    )

    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Inspect the workspace configuration")
        conversation = pilot.app.query_one(ConversationView)
        await conversation.apply_action(
            AppendToolStarted(
                task_id="task-1",
                call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments_json='{"path":"settings.json"}',
            )
        )
        await conversation.apply_action(
            UpdateToolCompleted(
                task_id="task-1",
                call_id=tool_call.id,
                tool_name=tool_call.name,
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
    tool_call = ToolCall(
        id="call-settings",
        name="read_workspace_config",
        arguments={"path": "settings.json"},
    )

    async def prepare(pilot) -> None:
        await wait_ready(pilot)
        await submit_text(pilot, "Inspect the workspace configuration")
        publish(pilot, "user-input", input_started())
        publish(
            pilot,
            "agent",
            AgentTextDeltaEvent(
                task_id="task-1",
                step=1,
                text="I will read the workspace configuration first.",
            ),
        )
        publish(
            pilot,
            "agent",
            AgentToolStartedEvent(
                task_id="task-1",
                step=1,
                tool_call=tool_call,
            ),
        )
        publish(
            pilot,
            "agent",
            AgentToolCompletedEvent(
                task_id="task-1",
                step=1,
                tool_call=tool_call,
                result=ToolExecutionResult(
                    success=False,
                    error="permission denied",
                ),
            ),
        )
        publish(
            pilot,
            "agent",
            TaskErrorEvent(
                task_id="task-1",
                fatal=True,
                code="agent_run_failed",
                step=1,
                error_type="ToolExecutionError",
                error_message="Could not read settings.json",
            ),
        )
        publish(
            pilot,
            "user-input",
            InputFinishedEvent(
                task_id="task-1",
                status="failed",
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
            AgentTextDeltaEvent(
                task_id="task-1",
                step=1,
                text=expected_markdown,
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
