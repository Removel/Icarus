"""FastMCP-independent data exchanged inside MCPPlugin."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class MCPServerInfo:
    name: str
    title: str | None = None
    version: str | None = None
    supports_tools: bool = False
    supports_resources: bool = False
    supports_prompts: bool = False


@dataclass(frozen=True)
class MCPToolDescriptor:
    tool_ref: str
    server: str
    name: str
    description: str
    input_schema: Mapping[str, Any]
    title: str | None = None
    annotations: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_ref": self.tool_ref,
            "server": self.server,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": _plain(dict(self.input_schema)),
            "annotations": _plain(dict(self.annotations)),
        }


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True)
class MCPToolCatalogSnapshot:
    generation: int
    tools: tuple[MCPToolDescriptor, ...]
    by_ref: Mapping[str, MCPToolDescriptor]


@dataclass(frozen=True)
class MCPContent:
    type: Literal["text", "image", "audio", "resource", "resource_link", "unknown"]
    data: Any
    media_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPCallResult:
    content: tuple[MCPContent, ...] = ()
    structured_content: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    is_error: bool = False
