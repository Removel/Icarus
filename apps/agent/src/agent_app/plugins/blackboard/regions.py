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


from icarus.core.contracts.context import (
    RegionLifetime, RegionDefinition, RegionInput, RegionOutput, RegionState,
    RegionSnapshot, _copy_object, _json_size, _section_dict, _freeze_json, _plain_json,
)


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

    def remove_inactive_owners(self, active_plugin_ids: set[str]) -> None:
        with self._lock:
            self._definitions = {key: value for key, value in self._definitions.items()
                                 if value[1].owner_plugin_id in active_plugin_ids}

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


