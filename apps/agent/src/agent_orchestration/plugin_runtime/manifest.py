"""Plugin Manifest models and dependency validation helpers."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Literal

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, model_validator


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._-]*$")
_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$"
)
_EVENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
PluginStateScope = Literal["workspace", "session"]


def _validate_id(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a normalized identifier")
    return normalized


def _validate_version(value: str, field_name: str) -> str:
    normalized = value.strip()
    try:
        Version(normalized)
    except InvalidVersion as error:
        raise ValueError(f"{field_name} must be a valid version") from error
    return normalized


class RequiredCapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str
    capability_id: str
    version_spec: str

    @model_validator(mode="after")
    def validate_fields(self) -> "RequiredCapabilityManifest":
        object.__setattr__(self, "plugin_id", _validate_id(self.plugin_id, "plugin_id"))
        object.__setattr__(
            self,
            "capability_id",
            _validate_id(self.capability_id, "capability_id"),
        )
        try:
            SpecifierSet(self.version_spec)
        except Exception as error:
            raise ValueError("version_spec must be a valid version specifier") from error
        return self


class ProvidedCapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str
    version: str

    @model_validator(mode="after")
    def validate_fields(self) -> "ProvidedCapabilityManifest":
        object.__setattr__(
            self,
            "capability_id",
            _validate_id(self.capability_id, "capability_id"),
        )
        object.__setattr__(self, "version", _validate_version(self.version, "version"))
        return self


class PluginManifest(BaseModel):
    """Static declaration loaded before Plugin code is imported."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    plugin_id: str
    plugin_version: str
    entrypoint: str
    python_requires: tuple[str, ...]
    required_capabilities: tuple[RequiredCapabilityManifest, ...]
    provided_capabilities: tuple[ProvidedCapabilityManifest, ...]
    provided_tools: tuple[str, ...]
    published_events: tuple[str, ...]
    consumed_events: tuple[str, ...]
    state_scopes: tuple[PluginStateScope, ...]
    workspace_state_version: int | None = Field(default=None, ge=1)
    session_state_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_contract(self) -> "PluginManifest":
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        object.__setattr__(self, "plugin_id", _validate_id(self.plugin_id, "plugin_id"))
        object.__setattr__(
            self,
            "plugin_version",
            _validate_version(self.plugin_version, "plugin_version"),
        )
        if not _ENTRYPOINT_PATTERN.fullmatch(self.entrypoint):
            raise ValueError("entrypoint must use <python-module>:<factory-function>")
        for requirement in self.python_requires:
            Requirement(requirement)
        for tool_name in self.provided_tools:
            _validate_id(tool_name, "provided tool name")
        for event_path in (*self.published_events, *self.consumed_events):
            if not _EVENT_PATTERN.fullmatch(event_path) or "." not in event_path:
                raise ValueError("event declarations must be full Python class paths")
        self._require_unique(self.python_requires, "python_requires")
        self._require_unique(
            (item.capability_id for item in self.provided_capabilities),
            "provided_capabilities",
        )
        self._require_unique(self.provided_tools, "provided_tools")
        self._require_unique(self.published_events, "published_events")
        self._require_unique(self.consumed_events, "consumed_events")
        self._require_unique(self.state_scopes, "state_scopes")
        workspace = "workspace" in self.state_scopes
        session = "session" in self.state_scopes
        if workspace != (self.workspace_state_version is not None):
            raise ValueError("workspace state scope and version must be declared together")
        if session != (self.session_state_version is not None):
            raise ValueError("session state scope and version must be declared together")
        if self.state_scopes and not any(
            requirement.plugin_id == "persistence"
            and requirement.capability_id == "state_store"
            for requirement in self.required_capabilities
        ):
            raise ValueError(
                "state_scopes require persistence/state_store capability"
            )
        return self

    @staticmethod
    def _require_unique(values, field_name: str) -> None:
        values = tuple(values)
        if len(values) != len(set(values)):
            raise ValueError(f"{field_name} must not contain duplicates")

    def capability_version(self, capability_id: str) -> str | None:
        for capability in self.provided_capabilities:
            if capability.capability_id == capability_id:
                return capability.version
        return None


def parse_manifest_text(text: str) -> tuple[PluginManifest, str]:
    payload: Any = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Plugin manifest must be a JSON object")
    manifest = PluginManifest.model_validate(payload)
    return manifest, sha256(text.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> tuple[PluginManifest, str]:
    return parse_manifest_text(Path(path).read_text(encoding="utf-8"))
