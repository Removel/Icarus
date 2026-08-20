"""Textual application controller for one Icarus Agent Runtime session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static
from textual.worker import Worker

from apps.tui.src.chat_state import (
    ChatState,
    InterruptAction,
    RuntimePhase,
)
from apps.tui.src.event_pipeline import (
    FinishTurn,
    SetRuntimeStatus,
    ShowNotification,
    UiAction,
    create_default_projector_registry,
)
from apps.tui.src.event_pipeline.dispatcher import ProjectorRegistry
from apps.tui.src.widgets import (
    ConversationView,
    PersistentComposer,
    QueuePanel,
    RuntimeStatusBar,
)


class RuntimeSubscription(Protocol):
    async def next_event(self) -> tuple[str, object]:
        ...

    def close(self) -> None:
        ...


class RuntimeService(Protocol):
    session_id: str | None

    async def start(self) -> None:
        ...

    def subscribe_events(self) -> RuntimeSubscription:
        ...

    async def submit(
        self,
        prompt: str,
        input_images=None,
    ) -> SubmitAccepted:
        ...

    async def stop(self, timeout: float | None = 30) -> None:
        ...


class SubmitAccepted(Protocol):
    task_id: str
    queue_position: int


RuntimeFactory = Callable[[], Awaitable[RuntimeService]]


@dataclass
class RuntimeOutputReceived(Message):
    source_plugin_id: str
    event: object


@dataclass
class RuntimeStarted(Message):
    subscription: RuntimeSubscription


@dataclass
class RuntimeStartFailed(Message):
    error: BaseException


@dataclass
class RuntimeSubscriptionFailed(Message):
    error: BaseException


class IcarusTextualApp(App[int]):
    """Full-screen chat UI with a local one-active-task queue."""

    CSS_PATH = "styles.tcss"
    TITLE = "Icarus"
    BINDINGS = [
        Binding(
            "ctrl+c",
            "context_interrupt",
            show=False,
            priority=True,
        ),
        Binding("ctrl+d", "eof", show=False, priority=True),
        Binding(
            "pageup",
            "conversation_page_up",
            show=False,
            priority=True,
        ),
        Binding(
            "pagedown",
            "conversation_page_down",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+end",
            "conversation_end",
            show=False,
            priority=True,
        ),
    ]

    def __init__(
        self,
        *,
        runtime_factory: RuntimeFactory,
        workspace_path: str | Path,
        projector_registry: ProjectorRegistry | None = None,
    ) -> None:
        super().__init__()
        self.runtime_factory = runtime_factory
        self.service: RuntimeService | None = None
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.chat_state = ChatState()
        self.projector_registry = projector_registry
        self.subscription: RuntimeSubscription | None = None
        self._runtime_start_worker: Worker[Any] | None = None
        self._event_worker: Worker[Any] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._dispatch_scheduled = False
        self._accepting_input = True
        self._has_user_submission = False
        self._fatal_message = ""

    def compose(self) -> ComposeResult:
        yield Static("Icarus", id="app-title", markup=False)
        yield Static(str(self.workspace_path), id="workspace-label", markup=False)
        yield ConversationView(self.workspace_path, id="conversation")
        yield QueuePanel(id="queue-panel")
        with Vertical(id="composer-shell"):
            yield Static("❯", id="composer-prompt", markup=False)
            yield PersistentComposer(id="composer")
        yield RuntimeStatusBar(id="status-bar")

    def on_mount(self) -> None:
        self._update_responsive_classes(self.size.width, self.size.height)
        self.query_one(PersistentComposer).focus()
        self._refresh_status()
        self._runtime_start_worker = self.run_worker(
            self._start_runtime(),
            name="runtime-start",
            group="runtime",
            exit_on_error=False,
        )

    def on_resize(self, event: events.Resize) -> None:
        self._update_responsive_classes(event.size.width, event.size.height)

    def _update_responsive_classes(self, width: int, height: int) -> None:
        self.screen.set_class(width <= 70, "-narrow")
        self.screen.set_class(height <= 12, "-short")

    async def _start_runtime(self) -> None:
        try:
            service = await self.runtime_factory()
            self.service = service
            if not self._accepting_input:
                await service.stop()
                self.service = None
                return
            if self.projector_registry is None:
                self.projector_registry = create_default_projector_registry()
            await service.start()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self.post_message(RuntimeStartFailed(error))
            return

        try:
            subscription = service.subscribe_events()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            cleanup_error: BaseException | None = None
            try:
                await service.stop()
            except BaseException as stop_error:
                cleanup_error = stop_error
            else:
                self.service = None
            if cleanup_error is not None:
                error.add_note(
                    "Runtime cleanup after subscription failure also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            self.post_message(RuntimeStartFailed(error))
            return
        self.post_message(RuntimeStarted(subscription))

    async def on_runtime_started(self, message: RuntimeStarted) -> None:
        if not self._accepting_input:
            message.subscription.close()
            return
        self.subscription = message.subscription
        self.chat_state.mark_ready()
        service = self.service
        session_id = getattr(service, "session_id", None)
        self._refresh_status(
            (f"Session {session_id}" if session_id else "Ready")
            if self._has_user_submission
            else ""
        )
        self._event_worker = self.run_worker(
            self._consume_runtime_events(message.subscription),
            name="runtime-events",
            group="runtime",
            exit_on_error=False,
        )
        self._schedule_dispatch()

    async def on_runtime_start_failed(
        self, message: RuntimeStartFailed
    ) -> None:
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"{type(message.error).__name__}: {message.error}"
        )
        if self._has_user_submission:
            self._refresh_status(self._fatal_message)
            self.notify(
                f"Runtime failed to start: {message.error}",
                title="Icarus startup failed",
                severity="error",
            )

    async def _consume_runtime_events(
        self, subscription: RuntimeSubscription
    ) -> None:
        try:
            while True:
                source_plugin_id, event = await subscription.next_event()
                self.post_message(
                    RuntimeOutputReceived(source_plugin_id, event)
                )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self._accepting_input:
                self.post_message(RuntimeSubscriptionFailed(error))

    async def on_runtime_subscription_failed(
        self, message: RuntimeSubscriptionFailed
    ) -> None:
        if not self._accepting_input:
            return
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"Output stream closed: {type(message.error).__name__}: {message.error}"
        )
        self._refresh_status(self._fatal_message)
        self.notify(
            self._fatal_message,
            title="Icarus output failed",
            severity="error",
        )

    async def on_persistent_composer_submitted(
        self, message: PersistentComposer.Submitted
    ) -> None:
        if not self._accepting_input:
            return
        command = message.text.strip()
        if command.lower() in {"exit", "quit"}:
            self.request_shutdown(return_code=0)
            return

        self._has_user_submission = True
        self.chat_state.enqueue(message.text)
        await self._refresh_queue()
        self._refresh_status()
        if self.chat_state.phase == RuntimePhase.FAILED:
            self.notify(
                self._fatal_message or "Runtime initialization failed",
                title="Icarus startup failed",
                severity="error",
            )
        await self._dispatch_next()

    async def _dispatch_next(self) -> None:
        self._dispatch_scheduled = False
        prompt = self.chat_state.begin_dispatch()
        if prompt is None:
            return

        self._refresh_status("Submitting queued message")
        service = self.service
        if service is None:
            self.chat_state.fail_dispatch()
            self._fatal_message = "Runtime is unavailable"
            self._refresh_status(self._fatal_message)
            return
        try:
            accepted = await service.submit(prompt=prompt)
        except asyncio.CancelledError:
            self.chat_state.fail_dispatch()
            raise
        except BaseException as error:
            self.chat_state.fail_dispatch()
            self._fatal_message = f"Submit failed: {type(error).__name__}: {error}"
            self._refresh_status(self._fatal_message)
            self.notify(
                self._fatal_message,
                title="Message was not submitted",
                severity="error",
            )
            return

        accepted_prompt = self.chat_state.accept_dispatch(accepted.task_id)
        await self.query_one(ConversationView).append_user_message(
            accepted_prompt
        )
        await self._refresh_queue()
        self._refresh_status("Accepted by runtime")

    async def on_runtime_output_received(
        self, message: RuntimeOutputReceived
    ) -> None:
        projector_registry = self.projector_registry
        if projector_registry is None:
            raise RuntimeError("Runtime output arrived before projectors were ready")
        actions = projector_registry.project(
            message.source_plugin_id,
            message.event,
            active_task_id=self.chat_state.active_task_id,
        )
        for action in actions:
            await self._apply_action(action)

    async def _apply_action(self, action: UiAction) -> None:
        if isinstance(action, SetRuntimeStatus):
            self._refresh_status(action.text)
            return
        if isinstance(action, ShowNotification):
            self.notify(action.text, severity=action.level)
            return

        handled = await self.query_one(ConversationView).apply_action(action)
        if not handled:
            raise TypeError(f"Unhandled UiAction: {type(action).__name__}")

        if isinstance(action, FinishTurn):
            if not self.chat_state.finish_active(action.task_id):
                return
            self._refresh_status(
                "" if action.status == "completed" else "Task failed"
            )
            self._schedule_dispatch()

    def _schedule_dispatch(self) -> None:
        if self._dispatch_scheduled or not self.chat_state.can_dispatch:
            return
        self._dispatch_scheduled = True
        self.call_later(self._dispatch_next)

    async def _refresh_queue(self) -> None:
        await self.query_one(QueuePanel).show_pending(
            self.chat_state.pending_items
        )

    def _refresh_status(self, message: str = "") -> None:
        self.query_one(RuntimeStatusBar).set_status(
            self.chat_state.phase,
            pending_count=len(self.chat_state.pending),
            message=message or self._fatal_message,
            show_phase=(
                self._has_user_submission
                or self.chat_state.phase == RuntimePhase.STOPPING
            ),
        )

    def action_context_interrupt(self) -> None:
        if not self._accepting_input:
            return
        composer = self.query_one(PersistentComposer)
        action = self.chat_state.interrupt_action(composer.text)
        if action == InterruptAction.CLEAR_DRAFT:
            composer.clear_draft()
            self._refresh_status("Draft cleared")
            return
        if action == InterruptAction.RESTORE_PENDING:
            restored = self.chat_state.pop_pending_tail()
            if restored is None:
                return
            composer.restore_draft(restored)
            self.call_later(self._refresh_queue)
            self._refresh_status("Latest queued message restored")
            composer.focus()
            return
        if action == InterruptAction.NOTIFY_CANCEL_UNAVAILABLE:
            text = "Current Agent task cannot be cancelled by this Runtime yet."
            self._refresh_status(text)
            self.notify(text, title="Cancellation unavailable", severity="warning")
            return
        self.request_shutdown(return_code=0)

    def action_eof(self) -> None:
        composer = self.query_one(PersistentComposer)
        if composer.text:
            composer.action_delete_right()
            return
        self.request_shutdown(return_code=0)

    def action_conversation_page_up(self) -> None:
        self.query_one(ConversationView).page_up()

    def action_conversation_page_down(self) -> None:
        self.query_one(ConversationView).page_down()

    def action_conversation_end(self) -> None:
        self.query_one(ConversationView).resume_follow()

    def request_shutdown(self, *, return_code: int) -> None:
        if self._cleanup_task is not None:
            return
        self._accepting_input = False
        self.chat_state.begin_stopping()
        self._refresh_status("Stopping runtime")
        self._cleanup_task = asyncio.create_task(
            self._cleanup_and_exit(return_code),
            name="icarus-tui:cleanup",
        )

    async def _cleanup_and_exit(self, return_code: int) -> None:
        start_worker = self._runtime_start_worker
        if start_worker is not None:
            start_worker.cancel()
            try:
                await start_worker.wait()
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass

        subscription = self.subscription
        self.subscription = None
        if subscription is not None:
            subscription.close()

        event_worker = self._event_worker
        if event_worker is not None:
            event_worker.cancel()
            try:
                await event_worker.wait()
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass

        stop_error: BaseException | None = None
        service = self.service
        if service is not None:
            try:
                await service.stop()
            except BaseException as error:
                stop_error = error

        if stop_error is not None:
            self._fatal_message = (
                f"Cleanup failed: {type(stop_error).__name__}: {stop_error}"
            )
            return_code = return_code or 1
        self.exit(result=return_code, return_code=return_code)

    async def on_unmount(self, event: events.Unmount) -> None:
        del event
        cleanup = self._cleanup_task
        if cleanup is not None and not cleanup.done():
            await asyncio.shield(cleanup)
            return
        if cleanup is None:
            self._accepting_input = False
            if self.subscription is not None:
                self.subscription.close()
                self.subscription = None
            service = self.service
            if service is not None:
                await service.stop()
