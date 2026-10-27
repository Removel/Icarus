"""Textual messages exchanged between the app shell and ChatScreen."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from textual.message import Message

from packages.gateway_protocol import RuntimeUpdateModel, SessionHistoryModel
from apps.tui.src.domain.chat_state import DispatchReservation
from apps.tui.src.gateway_client.models import (
    SubmitAccepted,
    TaskOperationResult,
    UpdateSubscription,
)
from apps.tui.src.gateway_client.ports import RuntimeClient
from apps.tui.src.services.types import PreparedSession


@dataclass
class RuntimeOutputReceived(Message):
    update: RuntimeUpdateModel


@dataclass
class DispatchFinished(Message):
    service: RuntimeClient | None
    reservation: DispatchReservation
    result: SubmitAccepted | TaskOperationResult | None = None
    error: BaseException | None = None


@dataclass
class RuntimeStarted(Message):
    service: RuntimeClient
    subscription: UpdateSubscription
    history: tuple[RuntimeUpdateModel, ...] = ()
    history_cursor: int = 0


@dataclass
class RuntimeReconnected(Message):
    service: RuntimeClient | None
    epoch: int
    cause: BaseException
    subscription: UpdateSubscription | None = None
    history: SessionHistoryModel | None = None


@dataclass
class TaskStatusReceived(Message):
    service: RuntimeClient
    epoch: int
    task_id: str
    status: dict[str, Any]


@dataclass
class RuntimeStartFailed(Message):
    error: BaseException


@dataclass
class RuntimeSubscriptionFailed(Message):
    error: BaseException
    subscription: UpdateSubscription | None = None


@dataclass
class SessionActivationRequested(Message):
    prepared: PreparedSession
    service: RuntimeClient | None
    epoch: int
    completed: asyncio.Future[bool]
