"""Immutable tool definitions grouped by their source Plugin."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Self

from icarus.core.models.messages import ToolDefinition
from icarus.core.tools.tool_registry import ToolRegistry


RESIDENT_TOOLS = (
    "read",
    "write",
    "insert",
    "bash",
    "tool_search",
    "tool_execute",
    "mcp_tool_list",
    "mcp_tool_search",
    "mcp_tool_execute",
)
_RESIDENT_TOOL_NAMES = frozenset(RESIDENT_TOOLS)


@dataclass(frozen=True)
class ToolSurfaceEntry:
    name: str
    plugin_id: str
    definition: ToolDefinition


@dataclass(frozen=True)
class ToolSurfaceCatalog:
    _entries: tuple[ToolSurfaceEntry, ...] = ()
    _by_scope: Mapping[str, tuple[ToolSurfaceEntry, ...]] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )

    @property
    def entries(self) -> tuple[ToolSurfaceEntry, ...]:
        return tuple(deepcopy(entry) for entry in self._entries)

    @classmethod
    def build(
        cls,
        pairs: Iterable[tuple[str, str]],
        registry: ToolRegistry,
    ) -> Self:
        entries = []
        for name, plugin_id in sorted(pairs, key=lambda pair: (pair[1], pair[0])):
            if name in _RESIDENT_TOOL_NAMES:
                continue
            tool = registry.get(name)
            if tool is None:
                continue
            entries.append(
                ToolSurfaceEntry(name, plugin_id, deepcopy(tool.definition))
            )
        by_scope = {
            plugin_id: tuple(
                entry for entry in entries if entry.plugin_id == plugin_id
            )
            for plugin_id in sorted({entry.plugin_id for entry in entries})
        }
        return cls(tuple(entries), MappingProxyType(by_scope))

    def search(self, scope: str) -> tuple[ToolDefinition, ...]:
        return tuple(
            deepcopy(entry.definition)
            for entry in self._by_scope.get(scope, ())
        )

    def directory(self) -> tuple[str, ...]:
        return tuple(
            f"{scope}({len(entries)}): "
            + ", ".join(entry.name for entry in entries)
            for scope, entries in self._by_scope.items()
        )
