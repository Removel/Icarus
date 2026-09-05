"""Textual application controller for one Gateway-backed Session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import logging
from pathlib import Path
import tempfile
from typing import Any, Protocol

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static
from textual.worker import Worker, WorkerCancelled

from apps.tui.src.clipboard import (
    ClipboardImage,
    ClipboardImageReadError,
    read_clipboard_image,
)
from apps.tui.src.commands import parse_local_command
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
from packages.gateway_protocol import (
    DiscardEmptySessionResultModel,
    ResourceRefModel,
    RuntimeUpdateModel,
    SessionHistoryModel,
    SessionSummaryModel,
)
from apps.tui.src.screens import SessionPicker
from apps.tui.src.widgets import (
    ConversationView,
    PersistentComposer,
    QueuePanel,
    RuntimeStatusBar,
)


logger = logging.getLogger("icarus.tui.app")


class UpdateSubscription(Protocol):
    async def next_update(self) -> RuntimeUpdateModel:
        ...

    def close(self) -> None:
        ...


class RuntimeClient(Protocol):
    session_id: str | None
    workspace_key: str | None

    async def start(self) -> None:
        ...

    def subscribe_updates(self) -> UpdateSubscription:
        ...

    async def get_session_history(
        self, *, after_sequence: int = 0
    ) -> SessionHistoryModel:
        ...

    async def list_sessions(self) -> tuple[SessionSummaryModel, ...]:
        ...

    async def get_session_status(self) -> dict[str, Any]:
        ...

    async def discard_empty_session(
        self, session_id: str
    ) -> DiscardEmptySessionResultModel:
        ...

    async def submit(
        self,
        prompt: str,
        *,
        submission_id: str,
        resources: tuple[ResourceRefModel, ...] = (),
        display_text: str | None = None,
    ) -> SubmitAccepted:
        ...

    async def cancel_task(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> TaskOperationResult:
        ...

    async def reconnect(self) -> UpdateSubscription:
        ...

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        ...

    async def close(self) -> None:
        ...


class SubmitAccepted(Protocol):
    task_id: str
    queue_position: int


class TaskOperationResult(Protocol):
    task_id: str | None
    status: str


ClientFactory = Callable[[str | None, bool], Awaitable[RuntimeClient]]


@dataclass
class RuntimeOutputReceived(Message):
    update: RuntimeUpdateModel


@dataclass
class RuntimeStarted(Message):
    service: RuntimeClient
    subscription: UpdateSubscription
    history: tuple[RuntimeUpdateModel, ...] = ()
    history_cursor: int = 0


@dataclass
class RuntimeStartFailed(Message):
    error: BaseException


@dataclass
class RuntimeSubscriptionFailed(Message):
    error: BaseException
    subscription: UpdateSubscription | None = None


@dataclass(frozen=True)
class PreparedSession:
    service: RuntimeClient
    subscription: UpdateSubscription
    history: SessionHistoryModel


class SessionBusyError(RuntimeError):
    pass


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
        runtime_factory: ClientFactory,
        initial_session_id: str | None = None,
        workspace_path: str | Path,
        resource_root: str | Path | None = None,
        projector_registry: ProjectorRegistry | None = None,
    ) -> None:
        super().__init__()
        self.runtime_factory = runtime_factory
        self.initial_session_id = initial_session_id
        self.service: RuntimeClient | None = None
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.resource_root = (
            Path(resource_root).expanduser().resolve()
            if resource_root is not None
            else None
        )
        self.chat_state = ChatState()
        self.projector_registry = projector_registry
        self._last_sequence = 0
        self._completed_assistant_steps: set[tuple[str, int]] = set()
        self.subscription: UpdateSubscription | None = None
        self._runtime_start_worker: Worker[Any] | None = None
        self._event_worker: Worker[Any] | None = None
        self._clipboard_worker: Worker[Any] | None = None
        self._session_operation_worker: Worker[Any] | None = None
        self._clipboard_temp_directory: tempfile.TemporaryDirectory[
            str
        ] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._dispatch_scheduled = False
        self._accepting_input = True
        self._has_user_submission = False
        self._session_has_user_input = False
        self._fatal_message = ""
        self._fatal_failure = False
        self._early_updates: dict[str, list[RuntimeUpdateModel]] = {}

    def compose(self) -> ComposeResult:
        yield Static("ICARUS", id="app-title", markup=False)
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
            self._start_runtime,
            name="runtime-start",
            group="runtime",
            exit_on_error=False,
        )

    def on_resize(self, event: events.Resize) -> None:
        self._update_responsive_classes(event.size.width, event.size.height)

    def _update_responsive_classes(self, width: int, height: int) -> None:
        self.screen.set_class(width <= 70, "-narrow")
        self.screen.set_class(
            width <= 88,
            "-compact-logo",
        )
        self.screen.set_class(height <= 12, "-short")

    async def _start_runtime(self) -> None:
        try:
            if self.projector_registry is None:
                self.projector_registry = create_default_projector_registry()
            prepared = await self._prepare_session(
                self.initial_session_id,
                True,
                require_idle=False,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self.post_message(RuntimeStartFailed(error))
            return
        if not self._accepting_input:
            prepared.subscription.close()
            await prepared.service.close()
            return
        self.service = prepared.service
        self.post_message(
            RuntimeStarted(
                prepared.service,
                prepared.subscription,
                tuple(prepared.history.records),
                prepared.history.history_cursor,
            )
        )

    async def _prepare_session(
        self,
        session_id: str | None,
        create_if_missing: bool,
        *,
        require_idle: bool,
    ) -> PreparedSession:
        service: RuntimeClient | None = None
        subscription: UpdateSubscription | None = None
        try:
            service = await self.runtime_factory(session_id, create_if_missing)
            await service.start()
            subscription = service.subscribe_updates()
            history = await service.get_session_history(after_sequence=0)
            if require_idle:
                status = await service.get_session_status()
                if not self._status_is_idle(status):
                    raise SessionBusyError(
                        f"Session {service.session_id} is not idle"
                    )
            return PreparedSession(service, subscription, history)
        except BaseException as error:
            if service is not None:
                if create_if_missing and session_id is None and service.session_id:
                    cleanup_service = (
                        self.service
                        if self.service is not None
                        and self.service is not service
                        else service
                    )
                    try:
                        await cleanup_service.discard_empty_session(
                            service.session_id
                        )
                    except BaseException:
                        logger.debug(
                            "Unable to discard failed candidate Session",
                            exc_info=True,
                        )
                try:
                    if subscription is not None:
                        subscription.close()
                    await service.close()
                except BaseException as cleanup_error:
                    error.add_note(
                        "Candidate cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
            raise

    async def on_runtime_started(self, message: RuntimeStarted) -> None:
        if not self._accepting_input:
            try:
                message.subscription.close()
                await message.service.close()
            except Exception as error:
                logger.error(
                    "Unable to close late runtime subscription: %s: %s",
                    type(error).__name__,
                    error,
                )
            return
        self.service = message.service
        self.subscription = message.subscription
        conversation = self.query_one(ConversationView)
        if message.history:
            conversation.begin_history_restore()
            with self.batch_update():
                for update in message.history:
                    await self._project_runtime_update(
                        update, historical=True
                    )
                    if self._fatal_failure:
                        message.subscription.close()
                        conversation.finish_history_restore()
                        return
            conversation.finish_history_restore()
        self._last_sequence = max(
            self._last_sequence, message.history_cursor
        )
        self._has_user_submission = self._session_has_user_input
        self.chat_state.mark_ready()
        service = self.service
        session_id = getattr(service, "session_id", None)
        self._refresh_status(
            (f"Session {session_id}" if session_id else "Ready")
            if self._has_user_submission
            else ""
        )
        if self._fatal_failure:
            return
        self._event_worker = self.run_worker(
            partial(self._consume_runtime_events, message.subscription),
            name="runtime-events",
            group="runtime",
            exit_on_error=False,
        )
        self._schedule_dispatch()

    async def on_runtime_start_failed(
        self, message: RuntimeStartFailed
    ) -> None:
        if not self._accepting_input:
            return
        self._fatal_failure = True
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"{type(message.error).__name__}: {message.error}"
        )
        if self._has_user_submission:
            self._refresh_status(self._fatal_message)
            self._safe_notify(
                f"Runtime failed to start: {message.error}",
                title="Icarus startup failed",
                severity="error",
            )

    async def _consume_runtime_events(
        self, subscription: UpdateSubscription
    ) -> None:
        try:
            while True:
                update = await subscription.next_update()
                self.post_message(RuntimeOutputReceived(update))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self._accepting_input:
                self.post_message(
                    RuntimeSubscriptionFailed(error, subscription)
                )

    async def on_runtime_subscription_failed(
        self, message: RuntimeSubscriptionFailed
    ) -> None:
        if not self._accepting_input:
            return
        if (
            message.subscription is not None
            and message.subscription is not self.subscription
        ):
            logger.debug("Ignoring failure from an inactive subscription")
            return
        service = self.service
        if service is not None:
            try:
                subscription = await service.reconnect()
                history = await service.get_session_history(
                    after_sequence=self._last_sequence
                )
                for update in history.records:
                    await self._project_runtime_update(
                        update, historical=True
                    )
                self._last_sequence = max(
                    self._last_sequence, history.history_cursor
                )
                active_task_id = self.chat_state.active_task_id
                self.subscription = subscription
                self._fatal_failure = False
                self._fatal_message = ""
                self.chat_state.mark_ready()
                if active_task_id is not None:
                    await self._reconcile_task_status(
                        service, active_task_id
                    )
                self._event_worker = self.run_worker(
                    partial(self._consume_runtime_events, subscription),
                    name="runtime-events",
                    group="runtime",
                    exit_on_error=False,
                )
                self._refresh_status("Reconnected")
                self._schedule_dispatch()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        self._fatal_failure = True
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"Output stream closed: {type(message.error).__name__}: {message.error}"
        )
        self._refresh_status(self._fatal_message)
        self._safe_notify(
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
        local_text = message.text
        for image in message.images:
            local_text = local_text.replace(image.marker, "")
        local_command = parse_local_command(local_text)
        if local_command is not None:
            if message.images:
                self.query_one(PersistentComposer).restore_draft(
                    message.submission
                )
                self._safe_notify(
                    "Remove image attachments before running a Session command.",
                    title="Session command not run",
                    severity="warning",
                )
                return
            if not self._can_start_session_operation():
                self._notify_session_command_unavailable()
                return
            operation = (
                self._resume_session
                if local_command == "resume"
                else self._clear_session
            )
            self._session_operation_worker = self.run_worker(
                operation,
                name=f"session-{local_command}",
                group="session-operation",
                exclusive=True,
                exit_on_error=False,
            )
            return
        if self.chat_state.phase == RuntimePhase.SWITCHING:
            self.query_one(PersistentComposer).restore_draft(message.submission)
            self._notify_session_command_unavailable()
            return

        self._has_user_submission = True
        self.chat_state.enqueue(message.submission)
        if not await self._refresh_queue():
            return
        self._refresh_status()
        if self.chat_state.phase == RuntimePhase.FAILED:
            self._safe_notify(
                self._fatal_message or "Runtime initialization failed",
                title="Icarus startup failed",
                severity="error",
            )
        await self._dispatch_next()

    def _can_start_session_operation(self) -> bool:
        worker = self._session_operation_worker
        return bool(
            self._accepting_input
            and not self._fatal_failure
            and self.service is not None
            and self.subscription is not None
            and self.chat_state.can_run_session_command
            and (worker is None or worker.is_finished)
        )

    def _notify_session_command_unavailable(self) -> None:
        text = "Session commands are only available while idle."
        self._refresh_status(text)
        self._safe_notify(
            text,
            title="Session command not run",
            severity="warning",
        )
        self.query_one(PersistentComposer).focus()

    async def _begin_session_operation(self) -> RuntimeClient:
        service = self.service
        if service is None or not self.chat_state.can_run_session_command:
            raise SessionBusyError(
                "Session commands are only available while idle"
            )
        self.chat_state.begin_switching()
        self._refresh_status("Checking current Session")
        status = await service.get_session_status()
        if not self._status_is_idle(status):
            raise SessionBusyError("Current Session is not idle")
        return service

    async def _resume_session(self) -> None:
        try:
            service = await self._begin_session_operation()
            self._refresh_status("Loading Session list")
            sessions = await service.list_sessions()
            selected_session_id = await self.push_screen_wait(
                SessionPicker(
                    sessions,
                    current_session_id=service.session_id,
                )
            )
            if selected_session_id is None:
                self.chat_state.mark_ready()
                self._refresh_status()
                return
            current_status = await service.get_session_status()
            if not self._status_is_idle(current_status):
                raise SessionBusyError("Current Session is no longer idle")
            self._refresh_status("Restoring Session")
            prepared = await self._prepare_session(
                selected_session_id,
                False,
                require_idle=True,
            )
            await self._activate_session(prepared)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self.chat_state.phase == RuntimePhase.SWITCHING:
                self.chat_state.mark_ready()
            self._refresh_status("Session resume failed")
            self._safe_notify(
                f"Unable to resume Session: {error}",
                title="Session resume failed",
                severity="warning",
            )
        finally:
            if self._accepting_input:
                self.query_one(PersistentComposer).focus()

    async def _clear_session(self) -> None:
        try:
            await self._begin_session_operation()
            if not self._session_has_user_input:
                self.chat_state.mark_ready()
                self._refresh_status("Already in a new conversation")
                self._safe_notify(
                    "The current conversation is already empty.",
                    title="New conversation",
                )
                return
            self._refresh_status("Starting a new conversation")
            prepared = await self._prepare_session(
                None,
                True,
                require_idle=False,
            )
            await self._activate_session(prepared)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self.chat_state.phase == RuntimePhase.SWITCHING:
                self.chat_state.mark_ready()
            self._refresh_status("New conversation failed")
            self._safe_notify(
                f"Unable to start a new conversation: {error}",
                title="New conversation failed",
                severity="warning",
            )
        finally:
            if self._accepting_input:
                self.query_one(PersistentComposer).focus()

    async def _activate_session(self, prepared: PreparedSession) -> None:
        old_service = self.service
        old_subscription = self.subscription
        old_event_worker = self._event_worker
        old_session_id = (
            old_service.session_id if old_service is not None else None
        )
        old_session_was_empty = not self._session_has_user_input

        self._event_worker = None
        if old_event_worker is not None:
            old_event_worker.cancel()
            try:
                await old_event_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception:
                logger.warning(
                    "Previous Session event worker stopped with an error",
                    exc_info=True,
                )
        if old_subscription is not None:
            try:
                old_subscription.close()
            except Exception:
                logger.warning(
                    "Unable to close previous Session subscription",
                    exc_info=True,
                )

        self.service = prepared.service
        self.subscription = prepared.subscription
        self.chat_state = ChatState()
        self._last_sequence = 0
        self._completed_assistant_steps.clear()
        self._early_updates.clear()
        self._dispatch_scheduled = False
        self._fatal_failure = False
        self._fatal_message = ""
        self._session_has_user_input = False
        self._has_user_submission = False

        conversation = self.query_one(ConversationView)
        activated = False
        try:
            await conversation.reset()
            if prepared.history.records:
                conversation.begin_history_restore()
                with self.batch_update():
                    for update in prepared.history.records:
                        await self._project_runtime_update(
                            update, historical=True
                        )
                        if self._fatal_failure:
                            raise RuntimeError(self._fatal_message)
                conversation.finish_history_restore()
            self._last_sequence = max(
                self._last_sequence, prepared.history.history_cursor
            )
            self._has_user_submission = self._session_has_user_input
            self.chat_state.mark_ready()
            self._refresh_status(
                (f"Session {prepared.service.session_id}")
                if self._session_has_user_input
                else ""
            )
            self._event_worker = self.run_worker(
                partial(
                    self._consume_runtime_events, prepared.subscription
                ),
                name="runtime-events",
                group="runtime",
                exit_on_error=False,
            )
            activated = True
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if not self._fatal_failure:
                self._enter_fatal("Session activation", error)
        finally:
            if old_service is not None:
                try:
                    await old_service.close()
                except BaseException:
                    logger.warning(
                        "Unable to close previous Session client",
                        exc_info=True,
                    )
        if not activated:
            return
        if (
            old_session_was_empty
            and old_session_id is not None
            and old_session_id != prepared.service.session_id
        ):
            try:
                await prepared.service.discard_empty_session(old_session_id)
            except BaseException:
                logger.warning("Unable to discard previous empty Session", exc_info=True)

    @staticmethod
    def _status_is_idle(status: dict[str, Any]) -> bool:
        if status.get("lifecycle") not in {"ready", "unloaded"}:
            return False
        if status.get("active_task_ids"):
            return False
        return all(
            int(status.get(field, 0)) == 0
            for field in (
                "queued_task_count",
                "pending_event_count",
                "pending_plugin_event_count",
                "background_work_count",
            )
        )

    def on_persistent_composer_image_paste_requested(
        self, message: PersistentComposer.ImagePasteRequested
    ) -> None:
        del message
        if not self._accepting_input:
            return
        worker = self._clipboard_worker
        if worker is not None and not worker.is_finished:
            return
        self._clipboard_worker = self.run_worker(
            self._paste_clipboard_image,
            name="clipboard-image-paste",
            group="clipboard",
            exit_on_error=False,
        )

    async def _paste_clipboard_image(self) -> None:
        try:
            image = await asyncio.to_thread(read_clipboard_image)
        except asyncio.CancelledError:
            raise
        except ClipboardImageReadError as error:
            self._notify_image_paste_failure(error)
            return
        except Exception as error:
            self._notify_image_paste_failure(error)
            return

        if not self._accepting_input:
            return
        composer = self.query_one(PersistentComposer)
        if image is None:
            composer.paste_text_from_clipboard()
            return

        try:
            path = self._store_clipboard_image(image)
            composer.attach_image(path, owned_temporary_file=True)
        except Exception as error:
            self._notify_image_paste_failure(error)

    def _store_clipboard_image(self, image: ClipboardImage) -> Path:
        directory = self._clipboard_temp_directory
        if directory is None:
            root = self.resource_root
            if root is not None:
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory = tempfile.TemporaryDirectory(
                prefix="icarus-tui-clipboard-",
                dir=str(root) if root is not None else None,
            )
            self._clipboard_temp_directory = directory
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="image-",
            suffix=f".{image.extension}",
            dir=directory.name,
            delete=False,
        ) as file:
            file.write(image.data)
            path = Path(file.name)
        path.chmod(0o600)
        return path

    def _notify_image_paste_failure(self, error: BaseException) -> None:
        if not self._accepting_input:
            return
        self._safe_notify(
            f"Unable to paste clipboard image: {error}",
            title="Image paste failed",
            severity="warning",
        )

    async def _dispatch_next(self) -> None:
        self._dispatch_scheduled = False
        submission = self.chat_state.begin_dispatch()
        if submission is None:
            return

        self._refresh_status("Submitting queued message")
        if self._fatal_failure:
            return
        service = self.service
        if service is None:
            self.chat_state.fail_dispatch()
            self._fatal_message = "Runtime is unavailable"
            self._refresh_status(self._fatal_message)
            return
        try:
            accepted = await service.submit(
                prompt=submission.model_prompt(),
                submission_id=submission.submission_id,
                resources=tuple(
                    ResourceRefModel(
                        resource_id=self._resource_id(image),
                        media_type=None,
                    )
                    for image in submission.images
                ),
                display_text=submission.text,
            )
        except asyncio.CancelledError:
            self.chat_state.fail_dispatch()
            raise
        except BaseException as error:
            self.chat_state.fail_dispatch()
            self._fatal_message = f"Submit failed: {type(error).__name__}: {error}"
            self._refresh_status(self._fatal_message)
            self._safe_notify(
                self._fatal_message,
                title="Message was not submitted",
                severity="error",
            )
            return

        try:
            accepted_message = self.chat_state.accept_dispatch(
                accepted.task_id
            )
            self._delete_submission_images(accepted_message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._enter_fatal("accepted message rendering", error)
            return
        if not await self._refresh_queue():
            return
        self._refresh_status("Accepted by runtime")
        await self._flush_early_updates(accepted.task_id)
        if self.chat_state.active_task_id == accepted.task_id:
            try:
                await self._reconcile_task_status(service, accepted.task_id)
            except Exception:
                logger.debug(
                    "Unable to reconcile accepted task status",
                    exc_info=True,
                )

    async def on_runtime_output_received(
        self, message: RuntimeOutputReceived
    ) -> None:
        if self._fatal_failure or not self._accepting_input:
            return
        update = message.update
        if (
            self.chat_state.dispatch_in_progress
            and self.chat_state.active_task_id is None
            and update.task_id is not None
        ):
            self._early_updates.setdefault(update.task_id, []).append(update)
            return
        await self._project_runtime_update(update)

    async def _project_runtime_update(
        self,
        update: RuntimeUpdateModel,
        *,
        historical: bool = False,
    ) -> None:
        service = self.service
        current_session_id = getattr(service, "session_id", None)
        current_workspace_key = getattr(service, "workspace_key", None)
        if (
            current_session_id
            and current_workspace_key
            and (
                update.session_id != current_session_id
                or update.workspace_key != current_workspace_key
            )
        ):
            logger.debug(
                "Ignoring update for inactive Session: %s/%s",
                update.workspace_key,
                update.session_id,
            )
            return
        if update.type == "user.message":
            self._session_has_user_input = True
        raw_step = update.payload.get("step")
        assistant_key = (
            update.task_id,
            raw_step,
        )
        if (
            update.type == "assistant.text_delta"
            and update.task_id is not None
            and isinstance(raw_step, int)
            and assistant_key in self._completed_assistant_steps
        ):
            return
        if (
            update.sequence is not None
            and update.sequence <= self._last_sequence
        ):
            return
        if (
            update.sequence is not None
            and (
                update.sequence <= self._last_sequence
                or (
                    not historical
                    and update.sequence != self._last_sequence + 1
                )
            )
        ):
            self._enter_fatal(
                "RuntimeUpdate sequence",
                RuntimeError(
                    "Session history has a sequence gap: "
                    f"expected={self._last_sequence + 1} "
                    f"actual={update.sequence}"
                ),
            )
            return
        projector_registry = self.projector_registry
        if projector_registry is None:
            self._enter_fatal(
                "event projection",
                RuntimeError(
                    "Runtime output arrived before projectors were ready"
                ),
            )
            return
        try:
            actions = projector_registry.project(
                update,
                active_task_id=self.chat_state.active_task_id,
                include_unrelated=(
                    historical or update.sequence is not None
                ),
            )
        except Exception as error:
            self._enter_fatal("event projection", error)
            return
        for action in actions:
            if self._fatal_failure:
                break
            if (
                isinstance(action, SetRuntimeStatus)
                and (
                    historical
                    or action.task_id != self.chat_state.active_task_id
                )
            ):
                continue
            try:
                await self._apply_action(action)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._enter_fatal("UI action routing", error)
                break
        if (
            not self._fatal_failure
            and update.type == "assistant.message"
            and update.task_id is not None
            and isinstance(raw_step, int)
        ):
            self._completed_assistant_steps.add(assistant_key)
        if not self._fatal_failure and update.sequence is not None:
            self._last_sequence = update.sequence

    async def _flush_early_updates(self, task_id: str) -> None:
        del task_id
        updates = [
            update
            for buffered in self._early_updates.values()
            for update in buffered
        ]
        self._early_updates.clear()
        updates.sort(
            key=lambda update: (
                update.sequence is None,
                update.sequence or 0,
                update.occurred_at,
            )
        )
        for update in updates:
            await self._project_runtime_update(update)
            if self._fatal_failure:
                return

    async def _reconcile_task_status(
        self, service: RuntimeClient, task_id: str
    ) -> None:
        status = await service.get_task_status(task_id)
        lifecycle = status.get("lifecycle")
        if lifecycle not in {"completed", "failed", "cancelled"}:
            return
        update = RuntimeUpdateModel(
            workspace_key=str(getattr(service, "workspace_key", "unknown")),
            session_id=str(getattr(service, "session_id", "unknown")),
            task_id=task_id,
            type="task.finished",
            payload={
                "status": lifecycle,
                "run_id": status.get("run_id"),
            },
            occurred_at=datetime.now(UTC),
        )
        await self._project_runtime_update(update)

    async def _apply_action(self, action: UiAction) -> None:
        if isinstance(action, SetRuntimeStatus):
            self._refresh_status(action.text)
            return
        if isinstance(action, ShowNotification):
            self._safe_notify(action.text, severity=action.level)
            return

        try:
            handled = await self.query_one(ConversationView).apply_action(action)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._enter_fatal("ConversationView action", error)
            return
        if not handled:
            raise TypeError(f"Unhandled UiAction: {type(action).__name__}")

        if isinstance(action, FinishTurn):
            if not self.chat_state.finish_active(action.task_id):
                return
            self._refresh_status(
                {
                    "completed": "",
                    "failed": "Task failed",
                    "cancelled": "Task cancelled",
                }[action.status]
            )
            self._schedule_dispatch()

    def _schedule_dispatch(self) -> None:
        if self._dispatch_scheduled or not self.chat_state.can_dispatch:
            return
        self._dispatch_scheduled = True
        self.call_later(self._dispatch_next)

    async def _refresh_queue(self) -> bool:
        try:
            await self.query_one(QueuePanel).show_pending(
                self.chat_state.pending_items
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._enter_fatal("queue rendering", error)
            return False
        return True

    def _refresh_status(self, message: str = "") -> None:
        try:
            self._render_status(message)
        except Exception as error:
            self._enter_fatal(
                "status bar update",
                error,
                render_status=False,
            )

    def _render_status(self, message: str = "") -> None:
        visible_message = (
            self._fatal_message
            if self._fatal_failure
            else message or self._fatal_message
        )
        self.query_one(RuntimeStatusBar).set_status(
            self.chat_state.phase,
            pending_count=len(self.chat_state.pending),
            message=visible_message,
            show_phase=(
                self._has_user_submission
                or self.chat_state.phase == RuntimePhase.STOPPING
            ),
        )

    def _enter_fatal(
        self,
        stage: str,
        error: BaseException,
        *,
        render_status: bool = True,
    ) -> None:
        if self._fatal_failure:
            logger.error(
                "Additional TUI failure after fatal handling began: stage=%s error=%s: %s",
                stage,
                type(error).__name__,
                error,
            )
            return

        self._fatal_failure = True
        self._dispatch_scheduled = False
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"TUI {stage} failed: {type(error).__name__}: {error}"
        )
        logger.error(
            self._fatal_message,
            exc_info=(type(error), error, error.__traceback__),
        )

        event_worker = self._event_worker
        if event_worker is not None:
            event_worker.cancel()

        if render_status:
            try:
                self._render_status(self._fatal_message)
            except Exception as status_error:
                logger.error(
                    "Unable to render fatal TUI status: %s: %s",
                    type(status_error).__name__,
                    status_error,
                )
        self._safe_notify(
            self._fatal_message,
            title="Icarus interface failed",
            severity="error",
        )

    def _safe_notify(self, message: str, **kwargs: Any) -> None:
        try:
            self.notify(message, **kwargs)
        except Exception as error:
            logger.error(
                "Unable to display TUI notification: %s: %s",
                type(error).__name__,
                error,
            )

    def action_context_interrupt(self) -> None:
        if not self._accepting_input:
            return
        composer = self.query_one(PersistentComposer)
        action = self.chat_state.interrupt_action(composer.has_draft)
        if action == InterruptAction.CLEAR_DRAFT:
            composer.clear_draft()
            self._refresh_status("Draft cleared")
            return
        if action == InterruptAction.RESTORE_PENDING:
            restored = self.chat_state.pop_pending_tail()
            if restored is None:
                return
            composer.restore_draft(restored)
            self.call_later(self._refresh_queue_after_interrupt)
            self._refresh_status("Latest queued message restored")
            composer.focus()
            return
        if action == InterruptAction.CANCEL_ACTIVE:
            self.run_worker(
                self._cancel_active_task,
                name="cancel-active-task",
                group="task-control",
                exclusive=True,
                exit_on_error=False,
            )
            return
        if action == InterruptAction.NOTIFY_CANCEL_UNAVAILABLE:
            text = "Waiting for the Runtime to confirm the submitted task."
            self._refresh_status(text)
            self._safe_notify(
                text,
                title="Cancellation pending",
                severity="warning",
            )
            return
        self.request_shutdown(return_code=0)

    async def _cancel_active_task(self) -> None:
        task_id = self.chat_state.active_task_id
        service = self.service
        if task_id is None or service is None:
            return
        try:
            result = await service.cancel_task(task_id, "user_requested")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._refresh_status("Cancellation failed")
            self._safe_notify(
                f"Unable to cancel task: {type(error).__name__}: {error}",
                title="Cancellation failed",
                severity="warning",
            )
            return
        if result.status in {"accepted", "already_cancelling"}:
            if self.chat_state.mark_cancelling(task_id):
                self._refresh_status("Cancellation requested")
            return
        if result.status == "already_finished":
            self._refresh_status("Task already finished")
            return
        self._safe_notify(
            f"Unable to cancel task: {result.status}",
            title="Cancellation failed",
            severity="warning",
        )

    async def _refresh_queue_after_interrupt(self) -> None:
        await self._refresh_queue()

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
        cleanup_errors = await self._release_runtime_resources()
        if cleanup_errors:
            self._fatal_message = "Cleanup failed: " + "; ".join(
                cleanup_errors
            )
            logger.error(self._fatal_message)
            return_code = return_code or 1
        self.exit(result=return_code, return_code=return_code)

    async def _release_runtime_resources(self) -> list[str]:
        errors: list[str] = []

        session_worker = self._session_operation_worker
        self._session_operation_worker = None
        if session_worker is not None:
            session_worker.cancel()
            try:
                await session_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(self._cleanup_error("session operation", error))

        clipboard_worker = self._clipboard_worker
        self._clipboard_worker = None
        if clipboard_worker is not None:
            clipboard_worker.cancel()
            try:
                await clipboard_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(
                    self._cleanup_error("clipboard worker", error)
                )

        start_worker = self._runtime_start_worker
        self._runtime_start_worker = None
        if start_worker is not None:
            start_worker.cancel()
            try:
                await start_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(self._cleanup_error("runtime start worker", error))

        subscription = self.subscription
        self.subscription = None
        if subscription is not None:
            try:
                subscription.close()
            except Exception as error:
                errors.append(self._cleanup_error("subscription close", error))

        event_worker = self._event_worker
        self._event_worker = None
        if event_worker is not None:
            event_worker.cancel()
            try:
                await event_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(self._cleanup_error("event worker", error))

        service = self.service
        self.service = None
        if service is not None:
            session_id = service.session_id
            if session_id is not None:
                try:
                    await service.discard_empty_session(session_id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "Unable to discard empty Session during shutdown: %s: %s",
                        type(error).__name__,
                        error,
                    )
            try:
                await service.close()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                errors.append(self._cleanup_error("runtime stop", error))

        clipboard_directory = self._clipboard_temp_directory
        self._clipboard_temp_directory = None
        if clipboard_directory is not None:
            try:
                clipboard_directory.cleanup()
            except Exception as error:
                errors.append(
                    self._cleanup_error("clipboard files", error)
                )

        return errors

    def _resource_id(self, image) -> str:
        root = self.resource_root
        if root is None:
            return image.path.name
        try:
            return image.path.resolve().relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("Image is outside the controlled resource root") from error

    @staticmethod
    def _delete_submission_images(submission) -> None:
        for image in submission.images:
            if not image.owned_temporary_file:
                continue
            try:
                image.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Unable to remove accepted image resource")

    @staticmethod
    def _cleanup_error(stage: str, error: BaseException) -> str:
        return f"{stage}: {type(error).__name__}: {error}"

    async def on_unmount(self, event: events.Unmount) -> None:
        del event
        cleanup = self._cleanup_task
        if cleanup is not None and not cleanup.done():
            await asyncio.shield(cleanup)
            return
        if cleanup is None:
            self._accepting_input = False
            cleanup_errors = await self._release_runtime_resources()
            if cleanup_errors:
                logger.error(
                    "Cleanup during unmount failed: %s",
                    "; ".join(cleanup_errors),
                )
