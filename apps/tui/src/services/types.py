"""Shared service values that do not depend on Textual."""

from __future__ import annotations

from dataclasses import dataclass

from packages.gateway_protocol import SessionHistoryModel
from apps.tui.src.gateway_client.models import UpdateSubscription
from apps.tui.src.gateway_client.ports import RuntimeClient


@dataclass(frozen=True)
class PreparedSession:
    service: RuntimeClient
    subscription: UpdateSubscription
    history: SessionHistoryModel


class SessionBusyError(RuntimeError):
    pass
