"""Prompt contributions and queries independent of plugin implementations."""

from dataclasses import dataclass
import re
from typing import ClassVar, Literal, Protocol, runtime_checkable

from icarus.core.capabilities.base import CapabilityRequest, CapabilityResponse
from icarus.core.capabilities.catalog import CapabilityContract
from icarus.core.events import Event


PromptLayer = Literal["base", "workspace", "session", "persona", "external", "mode", "contributor", "runtime"]
STATIC_LAYERS = ("base", "workspace", "session", "persona", "external")
DYNAMIC_LAYERS = ("mode", "contributor", "runtime")
SESSION_INSTRUCTIONS_TYPE = "session.instructions"


class PromptError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PromptSection:
    section_id: str
    layer: PromptLayer
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.section_id, str) or not re.fullmatch(r"[a-z][a-z0-9._-]*", self.section_id):
            raise ValueError("Prompt section ID must be a normalized identifier")
        if self.layer not in (*STATIC_LAYERS, *DYNAMIC_LAYERS):
            raise ValueError("Unknown prompt layer")
        if not isinstance(self.content, str):
            raise TypeError("Prompt content must be a string")


@runtime_checkable
class PromptContributor(Protocol):
    def static_prompt_sections(self) -> tuple[PromptSection, ...]: ...


@dataclass(frozen=True, kw_only=True)
class PromptContributionEvent(Event):
    status: Literal["completed", "failed"]
    sections: tuple[PromptSection, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or self.status not in {"completed", "failed"}:
            raise ValueError("Prompt contribution requires a task and terminal status")


@dataclass(frozen=True, kw_only=True)
class PromptUpdateEvent(Event):
    sections: tuple[PromptSection, ...] = ()


@dataclass(frozen=True, kw_only=True)
class PromptReadResponse(CapabilityResponse):
    system_prompt: str = ""


@dataclass(frozen=True, kw_only=True)
class PromptReadRequest(CapabilityRequest):
    reason: Literal["start", "refresh"] = "start"
    capability_id: ClassVar[str] = "prompt.read"
    response_type: ClassVar[type[CapabilityResponse]] = PromptReadResponse

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.reason not in {"start", "refresh"}:
            raise ValueError("Unknown prompt read reason")


class PromptRead(CapabilityContract):
    capability_id = "prompt.read"
    access_mode = "event"
    request_type = PromptReadRequest
    response_type = PromptReadResponse


EVENTS = {
    "prompt.contribution": PromptContributionEvent,
    "prompt.update": PromptUpdateEvent,
}
CAPABILITIES = (PromptRead,)
