"""Explicit messages between retained chat UI and application runtime."""

from dataclasses import dataclass

from textual.message import Message

from apps.tui.src.domain.submission import DraftImage, PendingMessage
from apps.tui.src.services.chat.operations import Completion, OperationScope
from packages.gateway_protocol import RuntimeUpdateModel


@dataclass
class SubmitRequested(Message):
    submission: PendingMessage


@dataclass
class OperationCompleted(Message):
    result: Completion


@dataclass
class RuntimeOutputReceived(Message):
    update: RuntimeUpdateModel
    scope: OperationScope


@dataclass
class SubscriptionFailed(Message):
    scope: OperationScope
    error: Exception


@dataclass
class ChatChanged(Message):
    revision: int

    def can_replace(self, message: Message) -> bool:
        return isinstance(message, ChatChanged)


@dataclass
class PasteRequested(Message):
    request_id: str
    draft_token: int


@dataclass
class ImagesReleased(Message):
    images: tuple[DraftImage, ...]


@dataclass
class InterruptRequested(Message):
    has_draft: bool


@dataclass
class CopyTextRequested(Message):
    text: str
    snapshot: object = None
    generation: int = 0


@dataclass
class OpenLinkRequested(Message):
    url: str


@dataclass
class DesktopCompleted(Message):
    kind: str
    generation: int
    snapshot: object = None
    error: Exception | None = None


@dataclass
class ViewFailed(Message):
    error: Exception
