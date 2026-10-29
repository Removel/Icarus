"""Chat lifecycle and state transitions; all methods except close run serially."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from apps.tui.src.client.gateway import GatewayClientError, GatewayTransportError
from apps.tui.src.domain.chat_state import InterruptAction, RuntimePhase
from apps.tui.src.domain.commands import Enqueue, RunCommand, route_submission
from apps.tui.src.event_pipeline import FinishTurn, SetRuntimeStatus, ShowNotification
from apps.tui.src.services.chat.dispatch import settle_dispatch
from apps.tui.src.services.chat.operations import (
    Operation,
    OperationScope,
    PreparedSession,
)
from apps.tui.src.services.chat.updates import UpdateProcessor
from packages.gateway_protocol import RuntimeUpdateModel


class SessionBusyError(RuntimeError):
    pass


class ChatSession:
    def __init__(
        self,
        store,
        client_factory,
        attachments,
        projectors,
        commands,
        *,
        initial_session_id=None,
    ):
        self.store, self.factory, self.attachments = store, client_factory, attachments
        self.commands = commands
        self.initial_session_id = initial_session_id
        self.client = self.subscription = None
        self.epoch = 0
        self.pending = {}
        self.updates = UpdateProcessor(projectors)
        self.reader_token = ""
        self.stopping = False
        self.resume_request = None
        self.sessions_ready = None
        self.clipboard_ready = None
        self.rejected = None
        self.draft_restore = None

    @property
    def scope(self):
        return OperationScope(
            self.store.generation, self.epoch, self.client, self.reader_token
        )

    def _operation(self, kind, arguments=None, *, client=None, reservation=None):
        scope = OperationScope(
            self.store.generation,
            self.epoch,
            self.client if client is None else client,
            uuid4().hex,
        )
        operation = Operation(kind, scope, arguments, reservation)
        self.pending[scope.operation_id] = operation
        return operation

    def start(self):
        return (
            self._operation(
                "prepare", (self.factory, self.initial_session_id, True, None)
            ),
        )

    def _status(self, text):
        self.store.status_message = text
        self.store.changed()

    def _dispatch(self):
        state = self.store.interaction
        reservation = state.begin_dispatch(self.epoch)
        if reservation is None:
            return ()
        self._status(
            "Submitting queued message"
            if reservation.mode == "submit"
            else "Adding queued message to current task"
        )
        try:
            resources = self.attachments.resources(reservation.message)
        except Exception as error:
            self._fatal(error)
            return ()
        return (
            self._operation(
                "dispatch", (reservation, resources), reservation=reservation
            ),
        )

    def submit(self, submission):
        if self.stopping:
            return ()
        decision = route_submission(
            submission,
            self.commands,
            phase=self.store.interaction.phase,
            can_run_session_command=self.store.interaction.can_run_session_command,
        )
        self.rejected = None
        if isinstance(decision, Enqueue):
            self.store.interaction.enqueue(submission)
            self.store.has_user_input = True
            self.store.changed()
            return self._dispatch()
        if isinstance(decision, RunCommand):
            match decision.definition.handler_name:
                case "resume_session":
                    return self.request_resume()
                case "clear_session":
                    return self.clear()
                case "exit_app":
                    self.stopping = True
                    return ()
            self._fatal(RuntimeError("Unknown command handler"))
            return ()
        self.rejected = submission
        self._status(decision.reason)
        return ()

    def receive(self, update, *, scope, historical=False):
        if (
            self.stopping
            or self.store.fatal_message
            or scope.generation != self.store.generation
            or scope.epoch != self.epoch
            or scope.client is not self.client
        ):
            return ()
        if not historical and scope.operation_id != self.reader_token:
            return ()
        state = self.store.interaction
        if (
            state.dispatch_in_progress
            and state.active_task_id is None
            and update.task_id is not None
            and not historical
        ):
            self.updates.early.append(update)
            return ()
        try:
            actions = self.updates.project(
                update,
                session_id=self.store.session_id,
                workspace_key=getattr(self.client, "workspace_key", None),
                active_task_id=state.active_task_id,
                historical=historical,
            )
            if actions is None:
                return ()
            self.store.apply_actions(actions, historical=historical)
            self.updates.commit(update)
            for action in actions:
                if isinstance(action, (SetRuntimeStatus, ShowNotification)):
                    self.store.status_message = action.text
                if isinstance(action, FinishTurn):
                    if state.finish_active(action.task_id):
                        self.store.status_message = ""
            return self._dispatch() if not historical else ()
        except Exception as error:
            self._fatal(error)
            return ()

    def complete(self, result):
        op, value, error = result.operation, result.value, result.error
        if self.pending.pop(op.scope.operation_id, None) is not op:
            return ()
        valid = (
            not self.stopping
            and op.scope.generation == self.store.generation
            and op.scope.epoch == self.epoch
            and op.scope.client is self.client
        )
        if op.kind in {"close", "discard"}:
            return ()
        # A dispatch sent on a lost connection must settle its exact reservation.
        reservation = op.reservation
        dispatch_current = (
            op.kind == "dispatch"
            and op.scope.generation == self.store.generation
            and op.scope.client is self.client
            and self.store.interaction.dispatch_reservation is reservation
        )
        if not valid and not dispatch_current:
            if isinstance(value, PreparedSession) and value.client is not self.client:
                value.subscription.close()
                return (self._operation("close", client=value.client),)
            if op.kind == "clipboard" and value and value.image:
                self.attachments.release((value.image,))
            return ()
        if error is not None:
            return self._error(op, error)
        if op.kind == "prepare":
            old = self.client
            old_empty = not self.store.has_user_input
            old_id = self.store.session_id
            if self.subscription:
                self.subscription.close()
            self.client, self.subscription = value.client, value.subscription
            self.epoch += 1
            self.reader_token = uuid4().hex
            pending_messages = (
                tuple(self.store.interaction.pending) if old is None else ()
            )
            self.store.reset_session(generation=self.store.generation + 1)
            for message in pending_messages:
                self.store.interaction.enqueue(message)
            self.store.session_id = value.client.session_id
            self.updates.reset()
            for update in value.history.records:
                self.receive(update, scope=self.scope, historical=True)
            self.updates.sequence = max(
                self.updates.sequence, value.history.history_cursor
            )
            if self.store.interaction.phase != RuntimePhase.FAILED:
                self.store.interaction.mark_ready()
            self.store.changed()
            cleanup = []
            if old is not None and old is not self.client:
                cleanup.append(self._operation("close", client=old))
                if old_empty and old_id and old_id != self.store.session_id:
                    cleanup.append(self._operation("discard", old_id))
            return tuple(cleanup) + self._dispatch()
        if op.kind == "dispatch":
            state = self.store.interaction
            if state.dispatch_reservation is not reservation:
                return ()
            try:
                decision = settle_dispatch(
                    state, reservation, epoch=self.epoch, value=value
                )
            except Exception as failure:
                self._fatal(failure)
                return ()
            if decision.blocked:
                self._status(f"Runtime rejected queued message: {decision.blocked}")
                return ()
            if decision.reconcile:
                self.store.changed()
                return (self._operation("status", decision.reconcile),)
            if decision.accepted:
                self.attachments.release(decision.accepted.images)
            self._status("Accepted by runtime")
            early, self.updates.early = self.updates.early, []
            operations = []
            for update in sorted(
                early,
                key=lambda u: (u.sequence is None, u.sequence or 0, u.occurred_at),
            ):
                operations.extend(
                    self.receive(update, scope=self.scope, historical=True)
                )
            if state.active_task_id:
                operations.append(self._operation("status", state.active_task_id))
            return tuple(operations) + self._dispatch()
        if op.kind == "reconnect":
            self.subscription = value.subscription
            self.reader_token = uuid4().hex
            for update in value.history.records:
                self.receive(update, scope=self.scope, historical=True)
            self.updates.sequence = max(
                self.updates.sequence, value.history.history_cursor
            )
            self.store.interaction.mark_ready()
            self._status("Reconnected")
            pending = self.store.interaction.dispatch_reservation
            if pending and pending.outcome_unknown:
                retry = self.store.interaction.begin_dispatch_retry(pending, self.epoch)
                return (
                    self._operation(
                        "dispatch",
                        (retry, self.attachments.resources(retry.message)),
                        reservation=retry,
                    ),
                )
            return self._dispatch()
        if op.kind == "status":
            if op.arguments == self.store.interaction.active_task_id and value.get(
                "lifecycle"
            ) in {"completed", "failed", "cancelled"}:
                update = RuntimeUpdateModel(
                    workspace_key=self.client.workspace_key,
                    session_id=self.store.session_id,
                    task_id=op.arguments,
                    type="task.finished",
                    payload={
                        "status": value["lifecycle"],
                        "run_id": value.get("run_id"),
                    },
                    occurred_at=datetime.now(UTC),
                )
                return self.receive(update, scope=self.scope)
        if op.kind == "cancel":
            if value.status in {"accepted", "already_cancelling"}:
                self.store.interaction.mark_cancelling(op.arguments)
                self._status("Cancellation requested")
        if op.kind == "list":
            self.sessions_ready = op.scope.operation_id, tuple(value)
        if op.kind == "clipboard":
            self.clipboard_ready = op.arguments, value
        self.store.changed()
        return ()

    def _error(self, op, error):
        if op.kind == "status":
            # Status lookup is advisory: the authoritative stream remains live.
            return ()
        if op.kind == "dispatch":
            reservation = op.reservation
            state = self.store.interaction
            if isinstance(error, GatewayTransportError):
                unknown = state.mark_dispatch_outcome_unknown(reservation)
                if self.epoch > unknown.attempt_epoch:
                    retry = state.begin_dispatch_retry(unknown, self.epoch)
                    return (
                        self._operation(
                            "dispatch",
                            (retry, self.attachments.resources(retry.message)),
                            reservation=retry,
                        ),
                    )
                return self.reconnect(error)
            if isinstance(error, GatewayClientError) and error.code in {
                "invalid_resource",
                "resource_unavailable",
                "invalid_content",
                "expired",
            }:
                state.block_dispatch(reservation, str(error))
                self._status(f"Runtime rejected queued message: {error.code}")
                return ()
        if op.kind in {"cancel", "list", "clipboard"} or (
            op.kind == "prepare" and self.client is not None
        ):
            if self.store.interaction.phase == RuntimePhase.SWITCHING:
                self.store.interaction.mark_ready()
            self._status(
                f"{dict(cancel='Cancellation', clipboard='Paste', list='Session list', prepare='Session').get(op.kind, op.kind)} failed: {error}"
            )
            return ()
        self._fatal(error)
        return ()

    def reconnect(self, cause):
        if self.stopping or any(o.kind == "reconnect" for o in self.pending.values()):
            return ()
        self.epoch += 1
        self.reader_token = ""
        self.sessions_ready = None
        self.resume_request = None
        self._status("Reconnecting")
        return (self._operation("reconnect", self.updates.sequence),)

    def cancel_active(self):
        task = self.store.interaction.active_task_id
        return (self._operation("cancel", task),) if task else ()

    def interrupt(self, has_draft):
        action = self.store.interaction.interrupt_action(has_draft)
        if action == InterruptAction.RESTORE_PENDING:
            self.draft_restore = self.store.interaction.pop_pending_tail()
            self.store.changed()
        return action

    def request_resume(self):
        if not self.store.interaction.can_run_session_command:
            return ()
        self.store.interaction.begin_switching()
        op = self._operation("list")
        self.resume_request = op.scope
        self.store.changed()
        return (op,)

    def resume(self, session_id, *, request_id):
        scope = self.resume_request
        self.resume_request = None
        self.sessions_ready = None
        if (
            not scope
            or scope.operation_id != request_id
            or scope.generation != self.store.generation
            or scope.epoch != self.epoch
        ):
            if (
                self.store.interaction.phase == RuntimePhase.SWITCHING
                and not self.stopping
            ):
                self.store.interaction.mark_ready()
                self.store.changed()
            return ()
        if session_id is None:
            self.store.interaction.mark_ready()
            self.store.changed()
            return ()
        return (
            self._operation("prepare", (self.factory, session_id, False, self.client)),
        )

    def clear(self):
        if not self.store.interaction.can_run_session_command:
            return ()
        if not self.store.has_user_input:
            self._status("Already in a new conversation")
            return ()
        self.store.interaction.begin_switching()
        self.store.changed()
        return (self._operation("prepare", (self.factory, None, True, self.client)),)

    def set_disclosure(self, task_id, kind, step, call_id, expanded):
        for unit in self.store.conversation.units:
            if unit.task_id == task_id and unit.kind == "run_card":
                for child in unit.children:
                    if child.kind == kind and (
                        (kind == "thinking" and child.step == step)
                        or (kind == "tool" and child.call_id == call_id)
                    ):
                        child.expanded = expanded

    def report_view_failure(self, error):
        if not self.store.fatal_message:
            self._fatal(error)

    def paste(self, *, request_id, draft_token):
        op = self._operation("clipboard", self.attachments)
        return (op,)

    def _fatal(self, error):
        self.store.fatal_message = str(error)
        self.store.interaction.mark_failed()
        self.store.changed()

    def begin_shutdown(self):
        self.stopping = True
        self.store.interaction.begin_stopping()
        self.store.changed()

    async def close(self):
        errors = []
        if self.subscription:
            try:
                self.subscription.close()
            except Exception as error:
                errors.append(str(error))
        client, self.client = self.client, None
        if client:
            try:
                await client.discard_empty_session(client.session_id)
            except Exception:
                pass
            try:
                await client.close()
            except Exception as error:
                errors.append(str(error))
        errors.extend(await self.attachments.close())
        if errors:
            self.store.fatal_message = "; ".join(errors)
        return tuple(errors)
