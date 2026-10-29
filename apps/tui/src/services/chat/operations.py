"""Captured I/O operations. Execution never mutates chat facts or widgets."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from apps.tui.src.client.gateway.ports import RuntimeClient, UpdateSubscriptionPort
from apps.tui.src.domain.chat_state import DispatchReservation
from packages.gateway_protocol import SessionHistoryModel


@dataclass(frozen=True)
class OperationScope:
    generation: int
    epoch: int
    client: RuntimeClient | None
    operation_id: str


@dataclass(frozen=True)
class PreparedSession:
    client: RuntimeClient
    subscription: UpdateSubscriptionPort
    history: SessionHistoryModel


@dataclass(frozen=True)
class Operation:
    kind: Literal[
        "prepare",
        "dispatch",
        "cancel",
        "status",
        "reconnect",
        "list",
        "clipboard",
        "close",
        "discard",
    ]
    scope: OperationScope
    arguments: Any = None
    reservation: DispatchReservation | None = None


@dataclass(frozen=True)
class Completion:
    operation: Operation
    value: Any = None
    error: Exception | None = None


async def execute(operation: Operation) -> Completion:
    client, args = operation.scope.client, operation.arguments
    try:
        match operation.kind:
            case "prepare":
                factory, session_id, create, source = args
                if source is not None:
                    from apps.tui.src.services.chat.status import status_is_idle

                    if not status_is_idle(await source.get_session_status()):
                        raise RuntimeError("Current Session is not idle")
                candidate = await factory(session_id, create)
                subscription = None
                try:
                    await candidate.start()
                    subscription = candidate.subscribe_updates()
                    history = await candidate.get_session_history(after_sequence=0)
                    if not create:
                        from apps.tui.src.services.chat.status import status_is_idle

                        if not status_is_idle(await candidate.get_session_status()):
                            raise RuntimeError("Target Session is not idle")
                    value = PreparedSession(candidate, subscription, history)
                except BaseException:
                    if (
                        create
                        and session_id is None
                        and getattr(candidate, "session_id", None)
                    ):
                        try:
                            await (source or candidate).discard_empty_session(
                                candidate.session_id
                            )
                        except Exception:
                            pass
                    if subscription is not None:
                        try:
                            subscription.close()
                        except Exception:
                            pass
                    await candidate.close()
                    raise
            case "dispatch":
                reservation, resources = args
                message = reservation.message
                kwargs = dict(
                    submission_id=message.submission_id,
                    resources=resources,
                    display_text=message.text,
                )
                if reservation.mode == "submit":
                    value = await client.submit(message.model_prompt(), **kwargs)
                else:
                    value = await client.steer_task(
                        reservation.task_id, message.model_prompt(), **kwargs
                    )
            case "cancel":
                value = await client.cancel_task(args, "user_requested")
            case "status":
                value = await client.get_task_status(args)
            case "reconnect":
                sub = await client.reconnect()
                value = PreparedSession(
                    client, sub, await client.get_session_history(after_sequence=args)
                )
            case "list":
                from apps.tui.src.services.chat.status import status_is_idle

                if not status_is_idle(await client.get_session_status()):
                    raise RuntimeError("Current Session is not idle")
                value = await client.list_sessions()
            case "clipboard":
                value = await args.read()
            case "close":
                await client.close()
                value = None
            case "discard":
                value = await client.discard_empty_session(args)
            case _:
                raise ValueError(f"Unknown chat operation: {operation.kind}")
        return Completion(operation, value)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        return Completion(operation, error=error)
