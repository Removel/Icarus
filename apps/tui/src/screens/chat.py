"""Main chat page: layout, focus, and page-level orchestration."""

from __future__ import annotations

import asyncio
from functools import partial
import logging
from pathlib import Path
from typing import Any

from textual import events
from textual.app import ComposeResult
from textual.screen import Screen
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static
from textual.worker import Worker, WorkerCancelled

from apps.tui.src.platform.clipboard import (
    ClipboardImageReadError,
    read_clipboard_image,
    read_clipboard_text,
)
from apps.tui.src.domain.commands import (
    CommandDefinition,
    CommandRegistry,
    create_default_command_registry,
)
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.chat_state import (
    ChatState,
    DispatchReservation,
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
from apps.tui.src.gateway_client.models import UpdateSubscription
from apps.tui.src.gateway_client.ports import ClientFactory, RuntimeClient
from apps.tui.src.services.clipboard_service import ClipboardService
from apps.tui.src.services.dispatch_service import DispatchOutcome, DispatchService
from apps.tui.src.services.session_service import SessionService
from apps.tui.src.services.submission_router import (
    Reject,
    RunCommand,
    SubmissionRouter,
    Unavailable,
)
from apps.tui.src.messages import (
    DispatchFinished,
    RuntimeOutputReceived,
    RuntimeReconnected,
    RuntimeStarted,
    RuntimeStartFailed,
    RuntimeSubscriptionFailed,
    SessionActivationRequested,
    TaskStatusReceived,
)
from apps.tui.src.services.types import PreparedSession
from apps.tui.src.services.update_stream import UpdateFatal, UpdateStream
from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.screens import SessionPicker
from apps.tui.src.widgets import (
    ConversationView,
    PersistentComposer,
    QueuePanel,
    RuntimeStatusBar,
    TurnRail,
)


logger = logging.getLogger("icarus.tui.app")




def _default_runtime_factory(config: TuiConfig) -> ClientFactory:
    from apps.tui.src.gateway_client import GatewayClient

    async def factory(
        session_id: str | None, create_if_missing: bool
    ) -> ClientFactory:
        return GatewayClient(
            url=config.gateway_url,
            workspace_path=config.workspace_path,
            session_id=session_id,
            create_if_missing=create_if_missing,
        )

    return factory


class ChatScreen(Screen[None]):
    """Full-screen chat UI with a local one-active-task queue."""

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
        config: TuiConfig,
        *,
        runtime_factory: ClientFactory | None = None,
        clipboard_reader=None,
        clipboard_text_reader=None,
        projector_registry: ProjectorRegistry | None = None,
        command_registry: CommandRegistry | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.runtime_factory = runtime_factory or _default_runtime_factory(config)
        self.initial_session_id = config.session_id
        self.chat_state = ChatState()
        self.workspace_path = config.workspace_path
        self.resource_root = config.resource_root
        self.session_service = SessionService(
            self.runtime_factory,
            self.config,
            chat_state=self.chat_state,
            has_user_input=lambda: self._session_has_user_input,
            on_status=self._refresh_status,
            on_notify=self._notify_dispatch,
        )
        self.projector_registry = (
            projector_registry or create_default_projector_registry()
        )
        self.command_registry = (
            command_registry or create_default_command_registry()
        )
        self.submission_router = SubmissionRouter(self.command_registry)
        self.clipboard_service = ClipboardService(
            self.resource_root,
            read_image=clipboard_reader or read_clipboard_image,
        )
        self._read_clipboard_text = (
            clipboard_text_reader or read_clipboard_text
        )
        self._runtime_start_worker: Worker[Any] | None = None
        self._event_worker: Worker[Any] | None = None
        self._reconnect_worker: Worker[Any] | None = None
        self._dispatch_worker: Worker[Any] | None = None
        self._clipboard_worker: Worker[Any] | None = None
        self._session_operation_worker: Worker[Any] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._dispatch_scheduled = False
        self._accepting_input = True
        self._has_user_submission = False
        self._session_has_user_input = False
        self._fatal_message = ""
        self._fatal_failure = False
        self.update_stream = UpdateStream(
            self.projector_registry,
            self.chat_state,
            session_scope=lambda: (
                getattr(self.service, "session_id", None),
                getattr(self.service, "workspace_key", None),
            ),
            on_actions=self._apply_actions,
            on_fatal=self._enter_fatal,
            on_user_input=self._mark_user_input,
        )
        self.dispatch_service = DispatchService(
            self.chat_state,
            lambda: self.service,
            resource_root=self.resource_root,
            on_status=self._refresh_status,
            on_notify=self._notify_dispatch,
        )

    @property
    def service(self) -> RuntimeClient | None:
        return self.session_service.client

    @property
    def subscription(self) -> UpdateSubscription | None:
        return self.session_service.subscription

    @property
    def _connection_epoch(self) -> int:
        return self.session_service.epoch

    @_connection_epoch.setter
    def _connection_epoch(self, value: int) -> None:
        self.session_service.epoch = value

    @property
    def _last_sequence(self) -> int:
        return self.update_stream.last_sequence

    @_last_sequence.setter
    def _last_sequence(self, value: int) -> None:
        self.update_stream.last_sequence = value

    def _mark_user_input(self) -> None:
        self._session_has_user_input = True

    def _notify_dispatch(
        self, message: str, title: str, severity: str
    ) -> None:
        self._safe_notify(message, title=title, severity=severity)

    async def _apply_actions(self, actions: tuple[UiAction, ...]) -> None:
        for action in actions:
            if self._fatal_failure:
                break
            await self._apply_action(action)

    def compose(self) -> ComposeResult:
        yield Static("ICARUS", id="app-title", markup=False)
        yield Static(str(self.workspace_path), id="workspace-label", markup=False)
        with Horizontal(id="conversation-shell"):
            yield ConversationView(self.workspace_path, id="conversation")
            yield TurnRail(id="turn-rail")
            yield Button("↓ New messages", id="new-output")
        yield QueuePanel(id="queue-panel")
        with Vertical(id="composer-shell"):
            yield Static("❯", id="composer-prompt", markup=False)
            yield PersistentComposer(id="composer")
        yield RuntimeStatusBar(id="status-bar")

    def _post_screen_message(self, message) -> None:
        message._tui_from_screen = True
        self.post_message(message)

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
        self.call_after_refresh(self._position_new_output)

    def _update_responsive_classes(self, width: int, height: int) -> None:
        self.set_class(width <= 70, "-narrow")
        self.set_class(
            width <= 88,
            "-compact-logo",
        )
        self.set_class(height <= 12, "-short")

    async def _start_runtime(self) -> None:
        result = await self.session_service.start(self.initial_session_id)
        if result.kind != "ready" or result.prepared is None:
            self._post_screen_message(
                RuntimeStartFailed(
                    result.error
                    or RuntimeError(result.message or "Runtime start failed")
                )
            )
            return
        prepared = result.prepared
        if not self._accepting_input:
            await self.session_service.release(prepared)
            return
        self._post_screen_message(
            RuntimeStarted(
                prepared.service,
                prepared.subscription,
                tuple(prepared.history.records),
                prepared.history.history_cursor,
            )
        )

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
        self.session_service.adopt_client(
            message.service, message.subscription
        )
        conversation = self.query_one(ConversationView)
        if message.history:
            conversation.begin_history_restore()
            with self.app.batch_update():
                for update in message.history:
                    await self._project_runtime_update(
                        update, historical=True
                    )
                    if self._fatal_failure:
                        message.subscription.close()
                        await conversation.finish_history_restore_async()
                        return
            await conversation.finish_history_restore_async()
            rail = self.query_one(TurnRail)
            rail.set_turns(
                [turn.text for turn in conversation.projection.turns],
                conversation.current_turn_index or 0,
            )
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
                self._post_screen_message(RuntimeOutputReceived(update))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self._accepting_input:
                self._post_screen_message(
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
        self._schedule_reconnect(message.error)

    def _schedule_reconnect(self, cause: BaseException) -> None:
        if not self._accepting_input:
            return
        worker = self._reconnect_worker
        if worker is not None and not worker.is_finished:
            return
        self._reconnect_worker = self.run_worker(
            partial(self._reconnect_runtime, cause),
            name="runtime-reconnect",
            group="runtime-reconnect",
            exclusive=True,
            exit_on_error=False,
        )

    async def _reconnect_runtime(self, cause: BaseException) -> None:
        service = self.service
        epoch = self._connection_epoch
        result = await self.session_service.reconnect(self._last_sequence)
        if result.kind == "reconnected" and result.prepared is not None:
            self._post_screen_message(
                RuntimeReconnected(
                    service,
                    epoch,
                    cause,
                    result.prepared.subscription,
                    result.prepared.history,
                )
            )
            return
        logger.debug(
            "Runtime reconnect failed: %s",
            result.error,
        )
        self._post_screen_message(RuntimeReconnected(service, epoch, cause))

    async def on_runtime_reconnected(self, message: RuntimeReconnected) -> None:
        subscription = message.subscription
        if (not self._accepting_input or message.service is not self.service
                or message.epoch != self._connection_epoch):
            if subscription is not None and subscription is not self.subscription:
                subscription.close()
            return
        service = message.service
        history = message.history
        if service is not None and subscription is not None and history is not None:
            for update in history.records:
                await self._project_runtime_update(update, historical=True)
                if self._fatal_failure:
                    return
            self._last_sequence = max(self._last_sequence, history.history_cursor)
            active_task_id = self.chat_state.active_task_id
            old_event_worker = self._event_worker
            if old_event_worker is not None:
                old_event_worker.cancel()
            self.session_service.adopt_client(service, subscription)
            self._fatal_failure = False
            self._fatal_message = ""
            self.chat_state.mark_ready()
            if active_task_id is not None:
                self._request_task_status(service, active_task_id)
            self._event_worker = self.run_worker(
                partial(self._consume_runtime_events, subscription),
                name="runtime-events",
                group="runtime",
                exit_on_error=False,
            )
            self._refresh_status("Reconnected")
            reservation = self.chat_state.dispatch_reservation
            if reservation is not None and reservation.outcome_unknown:
                retry = self.chat_state.begin_dispatch_retry(reservation, self._connection_epoch)
                self._start_reservation_worker(retry)
            else:
                self._schedule_dispatch()
            return
        self._fatal_failure = True
        self.chat_state.mark_failed()
        self._fatal_message = (
            f"Output stream closed: {type(message.cause).__name__}: {message.cause}"
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
        plan = self.submission_router.route(
            message.submission,
            phase=self.chat_state.phase,
            can_run_session_command=self._can_start_session_operation(),
        )
        if isinstance(plan, Reject):
            self._reject_command(
                message.submission, plan.reason, title=plan.title
            )
            return
        if isinstance(plan, Unavailable):
            self.query_one(PersistentComposer).restore_draft(
                message.submission
            )
            self._notify_session_command_unavailable()
            return
        if isinstance(plan, RunCommand):
            self._run_command(plan.definition)
            return

        self._has_user_submission = True
        self.chat_state.enqueue(plan.submission)
        if not await self._refresh_queue():
            return
        self._refresh_status()
        if self.chat_state.phase == RuntimePhase.FAILED:
            self._safe_notify(
                self._fatal_message or "Runtime initialization failed",
                title="Icarus startup failed",
                severity="error",
            )
        self._schedule_dispatch()

    def _reject_command(
        self,
        submission,
        reason: str,
        *,
        title: str,
    ) -> None:
        self.query_one(PersistentComposer).restore_draft(submission)
        self._refresh_status(reason)
        self._safe_notify(reason, title=title, severity="warning")

    def _run_command(self, definition: CommandDefinition) -> None:
        if definition.handler_name == "exit_app":
            self.request_shutdown(return_code=0)
            return
        operations = {
            "clear_session": self._clear_session,
            "resume_session": self._resume_session,
        }
        operation = operations.get(definition.handler_name)
        if operation is None:
            self._enter_fatal(
                "command dispatch",
                RuntimeError(
                    f"Unknown command handler: {definition.handler_name}"
                ),
            )
            return
        self._session_operation_worker = self.run_worker(
            operation,
            name=f"command-{definition.name.removeprefix('/')}",
            group="session-operation",
            exclusive=True,
            exit_on_error=False,
        )

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

    @staticmethod
    def _status_is_idle(status: dict[str, Any]) -> bool:
        return SessionService._status_is_idle(status)

    def _notify_session_command_unavailable(self) -> None:
        text = "Session commands are only available while idle."
        self._refresh_status(text)
        self._safe_notify(
            text,
            title="Session command not run",
            severity="warning",
        )
        self.query_one(PersistentComposer).focus()

    async def _resume_session(self) -> None:
        async def select_session(
            sessions, current_session_id: str
        ) -> str | None:
            return await self.app.push_screen_wait(
                SessionPicker(
                    sessions,
                    current_session_id=current_session_id,
                )
            )

        try:
            await self.session_service.resume(
                select_session,
                self._activate_session,
            )
        finally:
            if self._accepting_input:
                self.query_one(PersistentComposer).focus()

    async def _clear_session(self) -> None:
        try:
            await self.session_service.clear(self._activate_session)
        finally:
            if self._accepting_input:
                self.query_one(PersistentComposer).focus()

    async def _activate_session(self, prepared: PreparedSession) -> bool:
        old_service = self.service
        old_session_id = getattr(old_service, "session_id", None)
        old_session_was_empty = not self._session_has_user_input
        completed = asyncio.get_running_loop().create_future()
        self._post_screen_message(SessionActivationRequested(
            prepared, old_service, self._connection_epoch, completed
        ))
        activated = False
        try:
            activated = await completed
        finally:
            if self.service is not prepared.service:
                prepared.subscription.close()
                await prepared.service.close()
            elif old_service is not None:
                try:
                    await old_service.close()
                except Exception:
                    logger.warning("Unable to close previous Session client", exc_info=True)
        if (activated and old_session_was_empty and old_session_id is not None
                and old_session_id != prepared.service.session_id):
            try:
                await prepared.service.discard_empty_session(old_session_id)
            except Exception:
                logger.warning("Unable to discard previous empty Session", exc_info=True)
        return activated

    async def on_session_activation_requested(self, message: SessionActivationRequested) -> None:
        if (message.completed.cancelled() or not self._accepting_input
                or message.service is not self.service
                or message.epoch != self._connection_epoch):
            if not message.completed.done():
                message.completed.set_result(False)
            return
        try:
            activated = await self._apply_prepared_session(message.prepared)
        except asyncio.CancelledError:
            if not message.completed.done():
                message.completed.cancel()
            raise
        except Exception as error:
            if not message.completed.done():
                message.completed.set_exception(error)
        else:
            if not message.completed.done():
                message.completed.set_result(activated)

    async def _apply_prepared_session(self, prepared: PreparedSession) -> bool:
        old_subscription = self.subscription
        old_event_worker = self._event_worker

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

        self.session_service.adopt(prepared)
        self.chat_state = ChatState()
        self.session_service.bind_chat_state(self.chat_state)
        self.update_stream.bind_chat_state(self.chat_state)
        self.dispatch_service.bind_chat_state(self.chat_state)
        self.update_stream.reset()
        self._dispatch_scheduled = False
        self._fatal_failure = False
        self._fatal_message = ""
        self._session_has_user_input = False
        self._has_user_submission = False

        conversation = self.query_one(ConversationView)
        activated = False
        try:
            await conversation.reset()
            self.query_one("#new-output", Button).display = False
            self.query_one(TurnRail).set_turns((), 0)
            if prepared.history.records:
                conversation.begin_history_restore()
                with self.app.batch_update():
                    for update in prepared.history.records:
                        await self._project_runtime_update(
                            update, historical=True
                        )
                        if self._fatal_failure:
                            raise RuntimeError(self._fatal_message)
                await conversation.finish_history_restore_async()
                rail = self.query_one(TurnRail)
                rail.set_turns(
                    [turn.text for turn in conversation.projection.turns],
                    conversation.current_turn_index or 0,
                )
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
        return activated

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
            image = await asyncio.to_thread(
                self.clipboard_service.paste_image
            )
            text = (
                await asyncio.to_thread(self._read_clipboard_text)
                if image is None
                else None
            )
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
            if text is None:
                composer.paste_text_from_clipboard()
            elif text:
                composer.post_message(events.Paste(text))
            return

        try:
            composer.attach_image(
                image.path, owned_temporary_file=True
            )
        except Exception as error:
            self._notify_image_paste_failure(error)

    def _notify_image_paste_failure(self, error: BaseException) -> None:
        if not self._accepting_input:
            return
        self._safe_notify(
            f"Unable to paste clipboard content: {error}",
            title="Paste failed",
            severity="warning",
        )

    async def _dispatch_next(self) -> None:
        self._dispatch_scheduled = False
        reservation = self.chat_state.begin_dispatch(self._connection_epoch)
        if reservation is None:
            return
        await self._send_reservation(reservation)

    async def _send_reservation(
        self, reservation: DispatchReservation
    ) -> None:
        if self._fatal_failure:
            return
        service = self.service
        result, error = await self.dispatch_service.send_reservation(
            reservation
        )
        self._post_screen_message(
            DispatchFinished(service, reservation, result=result, error=error)
        )

    async def on_dispatch_finished(self, message: DispatchFinished) -> None:
        reservation = message.reservation
        if (not self._accepting_input or self._fatal_failure
                or message.service is not self.service
                or self.chat_state.dispatch_reservation is not reservation):
            return
        outcome = self.dispatch_service.handle_result(
            reservation,
            epoch=self._connection_epoch,
            result=message.result,
            error=message.error,
        )
        await self._apply_dispatch_outcome(outcome)

    async def _apply_dispatch_outcome(
        self, outcome: DispatchOutcome
    ) -> None:
        if outcome.fatal_error is not None:
            self._enter_fatal(
                outcome.fatal_stage or "message dispatch",
                outcome.fatal_error,
            )
            return
        if outcome.retry is not None:
            self._start_reservation_worker(outcome.retry)
            return
        if outcome.reconnect_cause is not None:
            self._schedule_reconnect(outcome.reconnect_cause)
            return
        if outcome.refresh_queue and not await self._refresh_queue():
            return
        if outcome.accepted_task_id is not None:
            await self._flush_early_updates(outcome.accepted_task_id)
            service = self.service
            if (service is not None
                    and self.chat_state.active_task_id
                    == outcome.accepted_task_id):
                self._request_task_status(
                    service, outcome.accepted_task_id
                )
        if outcome.reconcile_task_id is not None:
            service = self.service
            if service is not None:
                self._request_task_status(
                    service, outcome.reconcile_task_id
                )
        if outcome.schedule_next:
            self._schedule_dispatch()

    async def on_runtime_output_received(
        self, message: RuntimeOutputReceived
    ) -> None:
        if self._fatal_failure or not self._accepting_input:
            return
        await self._project_runtime_update(message.update)

    async def _project_runtime_update(
        self,
        update: RuntimeUpdateModel,
        *,
        historical: bool = False,
    ) -> None:
        try:
            await self.update_stream.project(update, historical=historical)
        except UpdateFatal as error:
            self._enter_fatal(error.stage, error.cause)

    async def _flush_early_updates(self, task_id: str) -> None:
        del task_id
        for update in self.update_stream.take_early():
            await self._project_runtime_update(update)
            if self._fatal_failure:
                return

    def _request_task_status(self, service: RuntimeClient, task_id: str) -> None:
        self.run_worker(
            partial(self._reconcile_task_status, service, task_id),
            name="task-status",
            group="task-status",
            exit_on_error=False,
        )

    async def _reconcile_task_status(
        self, service: RuntimeClient, task_id: str
    ) -> None:
        try:
            await self.update_stream.reconcile(service, task_id)
        except UpdateFatal as error:
            self._enter_fatal(error.stage, error.cause)

    async def on_task_status_received(self, message: TaskStatusReceived) -> None:
        if (not self._accepting_input or self._fatal_failure
                or message.service is not self.service
                or message.epoch != self._connection_epoch
                or message.task_id != self.chat_state.active_task_id):
            return
        try:
            await self.update_stream.project_status(
                message.service,
                message.task_id,
                message.status,
            )
        except UpdateFatal as error:
            self._enter_fatal(error.stage, error.cause)

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

        conversation = self.query_one(ConversationView)
        rail = self.query_one(TurnRail)
        self._sync_new_output(conversation.following_live_tail)
        rail.set_turns(
            [turn.text for turn in conversation.projection.turns],
            conversation.current_turn_index or 0,
        )
        if isinstance(action, FinishTurn):
            if not self.chat_state.finish_active(action.task_id):
                return
            self._refresh_status(
                {
                    "completed": "",
                    "failed": "Task failed",
                    "cancelled": "Task cancelled",
                    "interrupted": "Task interrupted",
                }[action.status]
            )
            self._schedule_dispatch()

    async def on_turn_rail_turn_selected(
        self, message: TurnRail.TurnSelected
    ) -> None:
        conversation = self.query_one(ConversationView)
        await conversation.jump_to_turn(message.index)
        self.query_one(TurnRail).set_turns(
            [turn.text for turn in conversation.projection.turns],
            message.index,
        )

    def on_conversation_view_follow_changed(
        self, message: ConversationView.FollowChanged
    ) -> None:
        """Returning to the tail dismisses the prompt."""

        # Showing it stays with _apply_action, which runs when output actually
        # arrives, so the label keeps meaning "new messages".
        if message.following:
            self._sync_new_output(True)

    def _sync_new_output(self, following: bool) -> None:
        conversation = self.query_one(ConversationView)
        indicator = self.query_one("#new-output", Button)
        if not following and conversation.turn_count:
            indicator.display = True
            self.call_after_refresh(self._position_new_output)
        else:
            indicator.display = False

    def _position_new_output(self) -> None:
        indicator = self.query_one("#new-output", Button)
        if not indicator.display:
            return
        conversation = self.query_one(ConversationView)
        shell = self.query_one("#conversation-shell", Horizontal)
        indicator.styles.offset = (
            max(
                0,
                conversation.region.right
                - shell.region.x
                - indicator.size.width
                - 2,
            ),
            2,
        )

    def on_button_pressed(self, message: Button.Pressed) -> None:
        if message.button.id != "new-output":
            return
        self.query_one(ConversationView).resume_follow()
        message.button.display = False

    def _schedule_dispatch(self) -> None:
        if self._dispatch_scheduled or not self.chat_state.can_dispatch:
            return
        self._dispatch_scheduled = True
        self.call_later(self._start_dispatch_worker)

    def _start_dispatch_worker(self) -> None:
        if not self._accepting_input:
            self._dispatch_scheduled = False
            return
        self._dispatch_worker = self.run_worker(
            self._dispatch_next,
            name="message-dispatch",
            group="message-dispatch",
            exit_on_error=False,
        )

    def _start_reservation_worker(self, reservation: DispatchReservation) -> None:
        self._dispatch_worker = self.run_worker(
            partial(self._send_reservation, reservation),
            name="message-dispatch",
            group="message-dispatch",
            exit_on_error=False,
        )

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
        outcome = await self.dispatch_service.cancel_active(
            epoch=self._connection_epoch
        )
        await self._apply_dispatch_outcome(outcome)

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
        self.app.exit(result=return_code, return_code=return_code)

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

        reconnect_worker = self._reconnect_worker
        self._reconnect_worker = None
        if reconnect_worker is not None:
            reconnect_worker.cancel()
            try:
                await reconnect_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(self._cleanup_error("reconnect worker", error))

        dispatch_worker = self._dispatch_worker
        self._dispatch_worker = None
        if dispatch_worker is not None:
            dispatch_worker.cancel()
            try:
                await dispatch_worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception as error:
                errors.append(self._cleanup_error("dispatch worker", error))

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

        errors.extend(await self.session_service.close())

        errors.extend(self.clipboard_service.cleanup())

        return errors

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
