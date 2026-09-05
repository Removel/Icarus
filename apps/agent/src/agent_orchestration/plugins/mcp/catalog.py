"""Immutable MCP Tool catalog snapshots and deterministic text search."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import replace
import json
import re
from types import MappingProxyType

from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPToolCatalogSnapshot,
    MCPToolDescriptor,
)


_TOKEN_PATTERN = re.compile(r"[\w.-]+", re.UNICODE)


class MCPToolCatalog:
    def __init__(self) -> None:
        self._snapshot = MCPToolCatalogSnapshot(
            generation=0, tools=(), by_ref=MappingProxyType({})
        )

    @property
    def snapshot(self) -> MCPToolCatalogSnapshot:
        return self._snapshot

    def replace_server(
        self, server: str, tools: Iterable[MCPToolDescriptor]
    ) -> MCPToolCatalogSnapshot:
        retained = [tool for tool in self._snapshot.tools if tool.server != server]
        normalized = [self._copy_descriptor(tool) for tool in tools]
        combined = tuple(sorted((*retained, *normalized), key=_sort_key))
        if _fingerprint(combined) == _fingerprint(self._snapshot.tools):
            return self._snapshot
        by_ref = {tool.tool_ref: tool for tool in combined}
        if len(by_ref) != len(combined):
            raise ValueError("MCP tool_ref values must be unique")
        self._snapshot = MCPToolCatalogSnapshot(
            generation=self._snapshot.generation + 1,
            tools=combined,
            by_ref=MappingProxyType(by_ref),
        )
        return self._snapshot

    def get(self, tool_ref: str) -> MCPToolDescriptor | None:
        return self._snapshot.by_ref.get(tool_ref)

    def list(
        self, *, server: str | None = None, page: int = 1, page_size: int = 20,
        exclude_servers: frozenset[str] = frozenset(),
    ) -> tuple[tuple[MCPToolDescriptor, ...], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        selected = tuple(
            tool for tool in self._snapshot.tools
            if (server is None or tool.server == server)
            and tool.server not in exclude_servers
        )
        start = (page - 1) * page_size
        return selected[start:start + page_size], len(selected)

    def search(
        self, query: str, *, server: str | None = None, limit: int = 5,
        exclude_servers: frozenset[str] = frozenset(),
    ) -> tuple[MCPToolDescriptor, ...]:
        query = query.strip().casefold()
        if not query:
            raise ValueError("query must be a non-empty string")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        tokens = tuple(_TOKEN_PATTERN.findall(query)) or (query,)
        scored = []
        for tool in self._snapshot.tools:
            if server is not None and tool.server != server:
                continue
            if tool.server in exclude_servers:
                continue
            score = _score(tool, query, tokens)
            if score > 0:
                scored.append((score, tool))
        scored.sort(key=lambda item: (-item[0], *_sort_key(item[1])))
        return tuple(tool for _, tool in scored[:limit])

    @staticmethod
    def _copy_descriptor(tool: MCPToolDescriptor) -> MCPToolDescriptor:
        return replace(
            tool,
            input_schema=_freeze_mapping(tool.input_schema),
            annotations=_freeze_mapping(tool.annotations),
            metadata=_freeze_mapping(tool.metadata),
        )


def tool_ref(server: str, tool_name: str) -> str:
    return f"{server}/{tool_name}"


def _sort_key(tool: MCPToolDescriptor) -> tuple[str, str, str]:
    return (tool.server.casefold(), tool.name.casefold(), tool.tool_ref)


def _score(
    tool: MCPToolDescriptor, query: str, tokens: tuple[str, ...]
) -> int:
    name = tool.name.casefold()
    ref = tool.tool_ref.casefold()
    server = tool.server.casefold()
    title = (tool.title or "").casefold()
    description = tool.description.casefold()
    if query in {name, ref}:
        return 10000
    score = 0
    for token in tokens:
        if token == name:
            score += 1000
        elif token in name or token in ref:
            score += 400
        if token and token in title:
            score += 100
        if token and token in description:
            score += 20
        if token == server:
            score += 5
    return score


def _fingerprint(tools: tuple[MCPToolDescriptor, ...]) -> str:
    return json.dumps(
        [tool.as_dict() | {"metadata": _plain_json(tool.metadata)} for tool in tools],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _freeze_mapping(value) -> MappingProxyType:
    return MappingProxyType(
        {str(key): _freeze_json(item) for key, item in deepcopy(dict(value)).items()}
    )


def _freeze_json(value):
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _plain_json(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value
