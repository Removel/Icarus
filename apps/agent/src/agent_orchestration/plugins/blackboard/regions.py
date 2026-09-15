"""Blackboard Region definitions, registration and current snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal, Mapping
from uuid import uuid4


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


class RegionRegistration:
    def __init__(self, registry: "RegionRegistry", token: str) -> None:
        self._registry = registry
        self._token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._registry._release(self._token)
        self._released = True


class RegionRegistry:
    """Mutable only while Plugin factories build one Runtime graph."""

    def __init__(self) -> None:
        self._definitions: dict[str, tuple[str, RegionDefinition]] = {}
        self._frozen = False
        self._lock = RLock()

    @property
    def is_frozen(self) -> bool:
        with self._lock:
            return self._frozen

    def register(self, definition: RegionDefinition) -> RegionRegistration:
        with self._lock:
            if self._frozen:
                raise RuntimeError("Region Registry is frozen")
            if definition.region in self._definitions:
                raise ValueError(f"Region is already registered: {definition.region}")
            token = uuid4().hex
            self._definitions[definition.region] = (token, definition)
            return RegionRegistration(self, token)

    def freeze(self) -> None:
        with self._lock:
            self._frozen = True

    def definitions(self) -> tuple[RegionDefinition, ...]:
        with self._lock:
            return tuple(item[1] for item in self._definitions.values())

    def get(self, region: str) -> RegionDefinition:
        with self._lock:
            try:
                return self._definitions[region][1]
            except KeyError as error:
                raise KeyError(f"Region is not registered: {region}") from error

    def _release(self, token: str) -> None:
        with self._lock:
            for region, (registered_token, _) in tuple(self._definitions.items()):
                if registered_token == token:
                    del self._definitions[region]
                    return


class RegionStore:
    """Keep one current snapshot for each registered Region."""

    def __init__(self, registry: RegionRegistry) -> None:
        self.registry = registry
        self._snapshots: dict[str, RegionSnapshot] = {}
        self._current_input_id: str | None = None
        self._lock = RLock()

    @property
    def current_input_id(self) -> str | None:
        with self._lock:
            return self._current_input_id

    def initialize(self) -> None:
        now = datetime.now(UTC)
        with self._lock:
            for definition in self.registry.definitions():
                if definition.lifetime == "session" and definition.region not in self._snapshots:
                    self._snapshots[definition.region] = _initial_snapshot(
                        definition, input_id=None, updated_at=now
                    )

    def begin_input(self, input_id: str) -> frozenset[str]:
        if not input_id.strip():
            raise ValueError("input_id cannot be empty")
        now = datetime.now(UTC)
        required: set[str] = set()
        with self._lock:
            self._current_input_id = input_id
            for definition in self.registry.definitions():
                if definition.lifetime != "input":
                    continue
                self._snapshots[definition.region] = _initial_snapshot(
                    definition, input_id=input_id, updated_at=now
                )
                if definition.required_for_start:
                    required.add(definition.region)
        return frozenset(required)

    def apply(
        self,
        *,
        source_plugin_id: str,
        task_id: str | None,
        current_task_id: str | None,
        region: str,
        input_id: str | None,
        input_value: RegionInput | None,
        output: RegionOutput | None,
        state: RegionState,
        complete_for_input: bool | None,
    ) -> RegionSnapshot:
        definition = self.registry.get(region)
        if source_plugin_id != definition.owner_plugin_id:
            raise PermissionError(f"Region update is not owned by {source_plugin_id}")
        if state.status not in definition.allowed_statuses:
            raise ValueError(f"Region status is not allowed: {state.status}")
        _validate_payload(definition, input_value, output)
        if definition.lifetime == "input":
            if not task_id or task_id != current_task_id:
                raise ValueError("Input Region update requires the current task_id")
            if not input_id or input_id != self.current_input_id:
                raise ValueError("Input Region update requires the current input_id")
            if not isinstance(complete_for_input, bool):
                raise ValueError("Input Region update requires complete_for_input")
        elif task_id is not None or input_id is not None or complete_for_input is not None:
            raise ValueError("Session Region update cannot carry task completion fields")
        snapshot = RegionSnapshot(
            region=definition.region,
            owner_plugin_id=definition.owner_plugin_id,
            lifetime=definition.lifetime,
            input_id=input_id,
            input=input_value,
            output=output,
            state=state,
            complete_for_input=complete_for_input,
            updated_at=datetime.now(UTC),
        )
        with self._lock:
            self._snapshots[region] = snapshot
        return snapshot

    def get(self, region: str) -> RegionSnapshot:
        with self._lock:
            try:
                return self._snapshots[region]
            except KeyError as error:
                self.registry.get(region)
                raise KeyError(f"Region has no current snapshot: {region}") from error

    def snapshots(self) -> tuple[RegionSnapshot, ...]:
        with self._lock:
            return tuple(
                self._snapshots[definition.region]
                for definition in self.registry.definitions()
                if definition.region in self._snapshots
            )

    def compact_view(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for definition in self.registry.definitions():
            if not definition.auto_expose:
                continue
            try:
                snapshot = self.get(definition.region)
            except KeyError:
                continue
            if snapshot.lifetime == "input" and snapshot.input_id != self.current_input_id:
                continue
            output = snapshot.output
            result[snapshot.region] = {
                "status": snapshot.state.status,
                "complete_for_input": snapshot.complete_for_input,
                "summary": output.summary if output else None,
                "refs": list(output.refs) if output else [],
                "error": output.error if output else None,
                "updated_at": snapshot.updated_at.isoformat(),
            }
        return result

    def restore_session_snapshots(self, values: object) -> None:
        if values is None:
            return
        if not isinstance(values, list):
            raise ValueError("Blackboard session_regions must be an array")
        restored: dict[str, RegionSnapshot] = {}
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Blackboard session Region must be an object")
            region = value.get("region")
            if not isinstance(region, str):
                raise ValueError("Blackboard session Region requires region")
            try:
                definition = self.registry.get(region)
            except KeyError:
                continue
            if definition.lifetime != "session":
                continue
            snapshot = _snapshot_from_dict(value, definition)
            _validate_payload(definition, snapshot.input, snapshot.output)
            restored[region] = snapshot
        with self._lock:
            self._snapshots.update(restored)

    def session_snapshot_values(self) -> list[dict[str, Any]]:
        return [
            snapshot.as_dict()
            for snapshot in self.snapshots()
            if snapshot.lifetime == "session"
        ]


def _initial_snapshot(
    definition: RegionDefinition, *, input_id: str | None, updated_at: datetime
) -> RegionSnapshot:
    return RegionSnapshot(
        region=definition.region,
        owner_plugin_id=definition.owner_plugin_id,
        lifetime=definition.lifetime,
        input_id=input_id,
        input=None,
        output=None,
        state=RegionState(definition.initial_status),
        complete_for_input=(False if definition.lifetime == "input" else None),
        updated_at=updated_at,
    )


def _validate_payload(
    definition: RegionDefinition,
    input_value: RegionInput | None,
    output: RegionOutput | None,
) -> None:
    for summary in (
        input_value.summary if input_value else None,
        output.summary if output else None,
    ):
        if summary is not None and len(summary) > definition.max_summary_chars:
            raise ValueError("Region summary exceeds its registered budget")
    if output and output.error is not None and len(output.error) > definition.max_summary_chars:
        raise ValueError("Region error exceeds its registered budget")
    if output and any(
        len(ref) > definition.max_summary_chars for ref in output.refs
    ):
        raise ValueError("Region ref exceeds its registered budget")
    if output and len(output.refs) > definition.max_refs:
        raise ValueError("Region refs exceed their registered budget")
    for data in (
        input_value.data if input_value else {},
        output.data if output else {},
    ):
        if _json_size(dict(data)) > definition.max_data_chars:
            raise ValueError("Region data exceeds its registered budget")


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


def _snapshot_from_dict(
    value: dict[str, Any], definition: RegionDefinition
) -> RegionSnapshot:
    owner = value.get("owner_plugin_id")
    lifetime = value.get("lifetime")
    if owner != definition.owner_plugin_id or lifetime != definition.lifetime:
        raise ValueError("Blackboard session Region does not match registration")
    state_value = value.get("state")
    if not isinstance(state_value, dict) or not isinstance(state_value.get("status"), str):
        raise ValueError("Blackboard session Region requires state")
    state = RegionState(state_value["status"])
    if state.status not in definition.allowed_statuses:
        raise ValueError("Blackboard session Region status is not allowed")
    input_value = _input_from_value(value.get("input"))
    output = _output_from_value(value.get("output"))
    updated_at = value.get("updated_at")
    if not isinstance(updated_at, str):
        raise ValueError("Blackboard session Region requires updated_at")
    return RegionSnapshot(
        region=definition.region,
        owner_plugin_id=definition.owner_plugin_id,
        lifetime=definition.lifetime,
        input_id=None,
        input=input_value,
        output=output,
        state=state,
        complete_for_input=None,
        updated_at=datetime.fromisoformat(updated_at),
    )


def _input_from_value(value: object) -> RegionInput | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Region input must be an object or null")
    return RegionInput(summary=value.get("summary"), data=value.get("data", {}))


def _output_from_value(value: object) -> RegionOutput | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Region output must be an object or null")
    return RegionOutput(
        summary=value.get("summary"),
        refs=tuple(value.get("refs", ())),
        data=value.get("data", {}),
        error=value.get("error"),
    )


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
