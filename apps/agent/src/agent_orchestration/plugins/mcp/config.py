"""Parse the small Icarus extension around common MCP server JSON."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import os
import re
from types import MappingProxyType
from typing import Any, Literal


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: Literal["stdio", "streamable-http"]
    raw: Mapping[str, Any]

    def resolved(self, *, workspace_path: str) -> dict[str, Any]:
        value = _expand_environment(deepcopy(dict(self.raw)))
        value["transport"] = self.transport
        if self.transport == "stdio" and not value.get("cwd"):
            value["cwd"] = workspace_path
        return value


def parse_mcp_servers(value: object) -> tuple[MCPServerConfig, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("mcpServers must be an object")
    servers: list[MCPServerConfig] = []
    names: set[str] = set()
    for raw_name, raw_config in value.items():
        name = str(raw_name).strip()
        if not name or "/" in name:
            raise ValueError("MCP server name must be non-empty and cannot contain '/'")
        if name != str(raw_name):
            raise ValueError("MCP server name cannot have surrounding whitespace")
        if name in names:
            raise ValueError(f"Duplicate MCP server name: {name}")
        names.add(name)
        if not isinstance(raw_config, Mapping):
            raise ValueError(f"MCP server config must be an object: {name}")
        config = deepcopy(dict(raw_config))
        enabled = config.pop("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"MCP server enabled must be a boolean: {name}")
        if not enabled:
            continue
        has_command = isinstance(config.get("command"), str) and bool(
            config["command"].strip()
        )
        has_url = isinstance(config.get("url"), str) and bool(
            config["url"].strip()
        )
        if has_command == has_url:
            raise ValueError(
                f"MCP server requires exactly one of command or url: {name}"
            )
        transport = "stdio" if has_command else "streamable-http"
        allowed = ("stdio",) if transport == "stdio" else ("http", "streamable-http")
        for field_name in ("transport", "type"):
            declared = config.get(field_name)
            if declared is not None and declared not in allowed:
                raise ValueError(
                    "MCP server transport does not match its connection "
                    f"fields: {name}"
                )
        servers.append(
            MCPServerConfig(
                name=name,
                transport=transport,
                raw=MappingProxyType(config),
            )
        )
    return tuple(servers)


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ValueError(f"MCP environment variable is not set: {name}")
            return os.environ[name]

        return _ENV_REFERENCE.sub(replace, value)
    if isinstance(value, Mapping):
        return {str(key): _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    return value
