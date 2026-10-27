"""Gateway Session lifecycle without Textual."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any, Literal

from packages.gateway_protocol import SessionSummaryModel
from apps.tui.src.domain.chat_state import ChatState, RuntimePhase
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.gateway_client.models import UpdateSubscription
from apps.tui.src.gateway_client.ports import ClientFactory, RuntimeClient
from apps.tui.src.services.types import PreparedSession, SessionBusyError


logger = logging.getLogger("icarus.tui.session_service")

SessionSelector = Callable[
    [tuple[SessionSummaryModel, ...], str],
    Awaitable[str | None],
]
SessionActivator = Callable[[PreparedSession], Awaitable[bool]]


@dataclass(frozen=True)
class SessionResult:
    kind: Literal["ready", "reconnected", "cancelled", "failed"]
    prepared: PreparedSession | None = None
    error: BaseException | None = None
    message: str = ""


class SessionService:
    """Prepare, activate, reconnect, and close one Gateway Session."""

    def __init__(
        self,
        runtime_factory: ClientFactory,
        config: TuiConfig,
        *,
        chat_state: ChatState,
        has_user_input: Callable[[], bool],
        on_status: Callable[[str], None],
        on_notify: Callable[[str, str, str], None],
    ) -> None:
        self._runtime_factory = runtime_factory
        self._config = config
        self.chat_state = chat_state
        self._has_user_input = has_user_input
        self._on_status = on_status
        self._on_notify = on_notify
        self._client: RuntimeClient | None = None
        self._subscription: UpdateSubscription | None = None
        self._epoch = 0

    @property
    def client(self) -> RuntimeClient | None:
        return self._client

    @property
    def subscription(self) -> UpdateSubscription | None:
        return self._subscription

    @property
    def epoch(self) -> int:
        return self._epoch

    @epoch.setter
    def epoch(self, value: int) -> None:
        self._epoch = value

    @property
    def session_id(self) -> str | None:
        client = self._client
        return getattr(client, "session_id", None) if client else None

    def adopt(self, prepared: PreparedSession) -> None:
        self.adopt_client(prepared.service, prepared.subscription)

    def bind_chat_state(self, chat_state: ChatState) -> None:
        self.chat_state = chat_state

    def adopt_client(
        self,
        client: RuntimeClient,
        subscription: UpdateSubscription,
    ) -> None:
        self._client = client
        self._subscription = subscription
        self._epoch += 1

    async def release(self, prepared: PreparedSession) -> None:
        prepared.subscription.close()
        await prepared.service.close()

    async def start(self, initial_session_id: str | None) -> SessionResult:
        try:
            prepared = await self._prepare(
                initial_session_id,
                create_if_missing=True,
                require_idle=False,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            return SessionResult(kind="failed", error=error)
        return SessionResult(kind="ready", prepared=prepared)

    async def resume(
        self,
        select_session: SessionSelector,
        activate: SessionActivator,
    ) -> SessionResult:
        try:
            client = await self._begin_session_operation()
            self._on_status("Loading Session list")
            sessions = await client.list_sessions()
            selected = await select_session(
                sessions, str(client.session_id or "")
            )
            if selected is None:
                self.chat_state.mark_ready()
                self._on_status("")
                return SessionResult(kind="cancelled")
            current_status = await client.get_session_status()
            if not self._status_is_idle(current_status):
                raise SessionBusyError("Current Session is no longer idle")
            self._on_status("Restoring Session")
            prepared = await self._prepare(
                selected,
                create_if_missing=False,
                require_idle=True,
            )
            activated = await activate(prepared)
            if activated:
                return SessionResult(kind="ready", prepared=prepared)
            return SessionResult(
                kind="failed",
                message="Session activation failed",
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self.chat_state.phase == RuntimePhase.SWITCHING:
                self.chat_state.mark_ready()
            self._on_status("Session resume failed")
            self._on_notify(
                f"Unable to resume Session: {error}",
                "Session resume failed",
                "warning",
            )
            return SessionResult(kind="failed", error=error)

    async def clear(self, activate: SessionActivator) -> SessionResult:
        try:
            if not self._has_user_input():
                self.chat_state.mark_ready()
                self._on_status("Already in a new conversation")
                self._on_notify(
                    "The current conversation is already empty.",
                    "New conversation",
                    "information",
                )
                return SessionResult(
                    kind="cancelled",
                    message="The current conversation is already empty.",
                )
            await self._begin_session_operation()
            self._on_status("Starting a new conversation")
            prepared = await self._prepare(
                None,
                create_if_missing=True,
                require_idle=False,
            )
            activated = await activate(prepared)
            if activated:
                return SessionResult(kind="ready", prepared=prepared)
            return SessionResult(
                kind="failed",
                message="Session activation failed",
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            if self.chat_state.phase == RuntimePhase.SWITCHING:
                self.chat_state.mark_ready()
            self._on_status("New conversation failed")
            self._on_notify(
                f"Unable to start a new conversation: {error}",
                "New conversation failed",
                "warning",
            )
            return SessionResult(kind="failed", error=error)

    async def reconnect(self, last_sequence: int) -> SessionResult:
        client = self._client
        if client is None:
            return SessionResult(
                kind="failed",
                error=RuntimeError("Runtime is unavailable"),
            )
        try:
            subscription = await client.reconnect()
            history = await client.get_session_history(
                after_sequence=last_sequence
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            return SessionResult(kind="failed", error=error)
        return SessionResult(
            kind="reconnected",
            prepared=PreparedSession(client, subscription, history),
        )

    async def close(self) -> list[str]:
        errors: list[str] = []
        subscription = self._subscription
        self._subscription = None
        if subscription is not None:
            try:
                subscription.close()
            except Exception as error:
                errors.append(_error_text("subscription close", error))

        client = self._client
        self._client = None
        if client is not None:
            session_id = getattr(client, "session_id", None)
            if session_id is not None:
                try:
                    await client.discard_empty_session(session_id)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "Unable to discard empty Session during shutdown:"
                        " %s: %s",
                        type(error).__name__,
                        error,
                    )
            try:
                await client.close()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                errors.append(_error_text("runtime stop", error))
        return errors

    async def _begin_session_operation(self) -> RuntimeClient:
        client = self._client
        if client is None or not self.chat_state.can_run_session_command:
            raise SessionBusyError(
                "Session commands are only available while idle"
            )
        self.chat_state.begin_switching()
        self._on_status("Checking current Session")
        status = await client.get_session_status()
        if not self._status_is_idle(status):
            raise SessionBusyError("Current Session is not idle")
        return client

    async def _prepare(
        self,
        session_id: str | None,
        *,
        create_if_missing: bool,
        require_idle: bool,
    ) -> PreparedSession:
        service: RuntimeClient | None = None
        subscription: UpdateSubscription | None = None
        try:
            service = await self._runtime_factory(
                session_id, create_if_missing
            )
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
                if (
                    create_if_missing
                    and session_id is None
                    and service.session_id
                ):
                    cleanup_service = (
                        self._client
                        if self._client is not None
                        and self._client is not service
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

    @staticmethod
    def _status_is_idle(status: dict[str, Any]) -> bool:
        if status.get("lifecycle") not in {"ready", "running", "unloaded"}:
            return False
        if status.get("active_task_ids"):
            return False
        return all(
            int(status.get(field, 0)) == 0
            for field in (
                "queued_task_count",
                "pending_event_count",
                "pending_plugin_event_count",
            )
        )


def _error_text(stage: str, error: BaseException) -> str:
    return f"{stage}: {type(error).__name__}: {error}"
