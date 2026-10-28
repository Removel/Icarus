"""Provider-neutral context and region value contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from types import MappingProxyType
from typing import Any, Literal, Mapping
from icarus.core.events import Event
from icarus.core.model_types import LLMRole
from icarus.core.models.messages import ImagePart, Message

RegionLifetime = Literal["input", "session"]

def _copy_object(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Region data must be an object")
    frozen = _freeze_json(deepcopy(dict(value)))
    _json_size(frozen)
    assert isinstance(frozen, Mapping)
    return frozen


def _json_size(value: object) -> int:
    try:
        return len(
            json.dumps(
                _plain_json(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Region data must be JSON serializable") from error


@dataclass(frozen=True)
class RegionDefinition:
    region: str
    owner_plugin_id: str
    lifetime: RegionLifetime
    required_for_start: bool = False
    auto_expose: bool = False
    allowed_statuses: tuple[str, ...] = ("idle",)
    initial_status: str = "idle"
    max_summary_chars: int = 500
    max_refs: int = 3
    max_data_chars: int = 6000

    def __post_init__(self) -> None:
        region = self.region.strip()
        owner = self.owner_plugin_id.strip()
        if any(not isinstance(item, str) for item in self.allowed_statuses):
            raise ValueError("Region allowed_statuses must contain strings")
        statuses = tuple(item.strip() for item in self.allowed_statuses)
        if not region or not owner:
            raise ValueError("Region name and owner_plugin_id cannot be empty")
        if self.lifetime not in {"input", "session"}:
            raise ValueError("Region lifetime must be input or session")
        if self.required_for_start and self.lifetime != "input":
            raise ValueError("Only input Regions can be required for start")
        if not statuses or any(not item for item in statuses):
            raise ValueError("Region allowed_statuses cannot be empty")
        if len(statuses) != len(set(statuses)):
            raise ValueError("Region allowed_statuses must be unique")
        if self.initial_status not in statuses:
            raise ValueError("Region initial_status must be allowed")
        for name in ("max_summary_chars", "max_refs", "max_data_chars"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"Region {name} must be a positive integer")
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "owner_plugin_id", owner)
        object.__setattr__(self, "allowed_statuses", statuses)


@dataclass(frozen=True)
class RegionInput:
    summary: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.summary is not None and not isinstance(self.summary, str):
            raise ValueError("Region input summary must be a string or null")
        object.__setattr__(self, "data", _copy_object(self.data))


@dataclass(frozen=True)
class RegionOutput:
    summary: str | None = None
    refs: tuple[str, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.summary is not None and not isinstance(self.summary, str):
            raise ValueError("Region output summary must be a string or null")
        if self.error is not None and not isinstance(self.error, str):
            raise ValueError("Region output error must be a string or null")
        if any(not isinstance(item, str) for item in self.refs):
            raise ValueError("Region refs must contain strings")
        refs = tuple(item.strip() for item in self.refs)
        if any(not item for item in refs) or len(refs) != len(set(refs)):
            raise ValueError("Region refs must be non-empty and unique")
        object.__setattr__(self, "refs", refs)
        object.__setattr__(self, "data", _copy_object(self.data))


@dataclass(frozen=True)
class RegionState:
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, str):
            raise ValueError("Region status must be a string")
        status = self.status.strip()
        if not status:
            raise ValueError("Region status cannot be empty")
        object.__setattr__(self, "status", status)


@dataclass(frozen=True)
class RegionSnapshot:
    region: str
    owner_plugin_id: str
    lifetime: RegionLifetime
    input_id: str | None
    input: RegionInput | None
    output: RegionOutput | None
    state: RegionState
    complete_for_input: bool | None
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "owner_plugin_id": self.owner_plugin_id,
            "lifetime": self.lifetime,
            "input_id": self.input_id,
            "input": _section_dict(self.input),
            "output": _section_dict(self.output),
            "state": asdict(self.state),
            "complete_for_input": self.complete_for_input,
            "updated_at": self.updated_at.isoformat(),
        }


def _section_dict(value: RegionInput | RegionOutput | None) -> dict[str, Any] | None:
    if value is None:
        return None
    result = {
        "summary": value.summary,
        "data": _plain_json(value.data),
    }
    if isinstance(value, RegionOutput):
        result["refs"] = list(value.refs)
        result["error"] = value.error
    return result


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Region data object keys must be strings")
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Region data must contain JSON values")


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


@dataclass(frozen=True, kw_only=True)
class ContextBlock:
    source_plugin_id: str
    context_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True, kw_only=True)
class ContextContributionEvent(Event):
    status: Literal["completed", "failed"]
    context_blocks: list[ContextBlock] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class BlackboardContextReadyEvent(Event):
    """Blackboard 聚合完成后发布的不可变上下文快照。"""

    model_role: LLMRole
    system_prompt: str
    input_prompt: str
    history_messages: list[Message] = field(default_factory=list)
    input_images: list[ImagePart] = field(default_factory=list)
    tools: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class BlackboardCompactedEvent(Event):
    before_tokens: int
    after_tokens: int


@dataclass(frozen=True, kw_only=True)
class BlackboardRegionUpdatedEvent(Event):
    region: str
    input_id: str | None
    input: RegionInput | None
    output: RegionOutput | None
    state: RegionState
    complete_for_input: bool | None
