"""Read the committed model-message state, never the public UI projection."""

from dataclasses import dataclass
from typing import ClassVar

from icarus.core.capabilities.base import CapabilityRequest, CapabilityResponse
from icarus.core.models.messages import Message


@dataclass(frozen=True, kw_only=True)
class ConversationReadResponse(CapabilityResponse):
    messages: tuple[Message, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ConversationReadRequest(CapabilityRequest):
    capability_id: ClassVar[str] = "conversation.read"
    response_type: ClassVar[type[CapabilityResponse]] = ConversationReadResponse
