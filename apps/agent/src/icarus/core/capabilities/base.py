"""Common request/response values for directed capability calls."""

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import ClassVar, Self
from uuid import uuid4

from icarus.core.events import Event


class CapabilityStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    FAILED = "failed"


class CapabilityCallError(RuntimeError):
    def __init__(self, response: "CapabilityResponse") -> None:
        super().__init__(response.error_message or response.status.value)
        self.status = response.status
        self.error_code = response.error_code


@dataclass(frozen=True, kw_only=True)
class CapabilityRequest(Event):
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    timeout_seconds: float | None = None
    capability_id: ClassVar[str]
    response_type: ClassVar[type["CapabilityResponse"]]

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("correlation_id cannot be empty")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True, kw_only=True)
class CapabilityResponse(Event):
    correlation_id: str
    status: CapabilityStatus
    error_code: str | None = None
    error_message: str | None = None

    def unwrap(self) -> Self:
        if self.status is not CapabilityStatus.OK:
            raise CapabilityCallError(self)
        return self
