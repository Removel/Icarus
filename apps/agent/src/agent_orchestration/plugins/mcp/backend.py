"""Thin FastMCP 4 client boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
import logging
from typing import Any, Protocol

from apps.agent.src.agent_orchestration.plugins.mcp.catalog import tool_ref
from apps.agent.src.agent_orchestration.plugins.mcp.config import MCPServerConfig
from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPServerInfo,
    MCPToolDescriptor,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor


ToolsChanged = Callable[[], Awaitable[None] | None]


class MCPClientBackend(Protocol):
    async def connect(self) -> MCPServerInfo: ...

    async def list_tools(self) -> tuple[MCPToolDescriptor, ...]: ...

    async def call_tool(
        self, name: str, arguments: Mapping[str, object]
    ) -> MCPCallResult: ...

    async def close(self) -> None: ...


class FastMCPClientBackend:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        workspace_path: str,
        tools_changed: ToolsChanged,
        logger: logging.Logger | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.config = config
        self.workspace_path = workspace_path
        self.tools_changed = tools_changed
        self.logger = logger or logging.getLogger("icarus.agent.mcp")
        self.redactor = redactor or Redactor()
        self._client: Any | None = None
        self._entered = False

    async def connect(self) -> MCPServerInfo:
        if self._entered:
            return self._server_info()
        try:
            from fastmcp import Client
            from fastmcp.client.messages import MessageHandler
        except ImportError as error:
            raise RuntimeError(
                "FastMCP client support is not installed"
            ) from error

        tools_changed = self.tools_changed
        logger = self.logger
        redactor = self.redactor

        class _Handler(MessageHandler):
            async def on_tool_list_changed(self, message) -> None:
                del message
                result = tools_changed()
                if result is not None:
                    await result

        async def log_handler(message) -> None:
            level = str(_field(message, "level") or "info").lower()
            log_method = getattr(
                logger,
                {"notice": "info", "alert": "critical", "emergency": "critical"}.get(
                    level, level
                ),
                logger.info,
            )
            data = redactor.redact(_json_value(_field(message, "data")))
            remote_logger = _field(message, "logger")
            log_method(
                "MCP server log: server=%s logger=%s data=%s",
                self.config.name,
                redactor.redact_text(str(remote_logger or "-")),
                data,
            )

        config = self.config.resolved(workspace_path=self.workspace_path)
        client = Client(
            {"mcpServers": {self.config.name: config}},
            name=f"icarus-{self.config.name}",
            message_handler=_Handler(),
            log_handler=log_handler,
            timeout=120,
            init_timeout=15,
        )
        try:
            await client.__aenter__()
        except BaseException:
            try:
                await asyncio.shield(client.close())
            except BaseException:
                pass
            raise
        self._client = client
        self._entered = True
        return self._server_info()

    async def list_tools(self) -> tuple[MCPToolDescriptor, ...]:
        client = self._require_client()
        tools = await client.list_tools()
        return tuple(self._convert_tool(tool) for tool in tools)

    async def call_tool(
        self, name: str, arguments: Mapping[str, object]
    ) -> MCPCallResult:
        result = await self._require_client().call_tool_mcp(
            name=name, arguments=dict(arguments)
        )
        return MCPCallResult(
            content=tuple(_convert_content(item) for item in result.content),
            structured_content=_json_value(
                _field(result, "structured_content", "structuredContent")
            ),
            metadata=_mapping(_field(result, "meta", "_meta")) or {},
            is_error=bool(_field(result, "is_error", "isError", default=False)),
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        self._entered = False
        if client is not None:
            await client.close()

    def _require_client(self):
        if not self._entered or self._client is None:
            raise RuntimeError(f"MCP server is not connected: {self.config.name}")
        return self._client

    def _server_info(self) -> MCPServerInfo:
        client = self._client
        initialize = client.initialize_result if client is not None else None
        info = (
            getattr(client, "server_info", None)
            if client is not None
            else None
        ) or _field(initialize, "server_info", "serverInfo")
        capabilities = (
            getattr(client, "server_capabilities", None)
            if client is not None
            else None
        ) or _field(initialize, "capabilities")
        return MCPServerInfo(
            name=self.config.name,
            title=_field(info, "title"),
            version=_field(info, "version"),
            supports_tools=_field(capabilities, "tools") is not None,
            supports_resources=_field(capabilities, "resources") is not None,
            supports_prompts=_field(capabilities, "prompts") is not None,
        )

    def _convert_tool(self, value: Any) -> MCPToolDescriptor:
        name = str(_field(value, "name") or "").strip()
        if not name:
            raise ValueError(f"MCP server returned a Tool without a name: {self.config.name}")
        schema = _field(value, "input_schema", "inputSchema", default={})
        if not isinstance(schema, Mapping):
            raise ValueError(
                f"MCP Tool inputSchema must be an object: {self.config.name}/{name}"
            )
        return MCPToolDescriptor(
            tool_ref=tool_ref(self.config.name, name),
            server=self.config.name,
            name=name,
            title=_field(value, "title"),
            description=str(_field(value, "description") or ""),
            input_schema=deepcopy(dict(schema)),
            annotations=_mapping(_field(value, "annotations")) or {},
            metadata=_mapping(_field(value, "meta", "_meta")) or {},
        )


def _convert_content(value: Any) -> MCPContent:
    kind = str(_field(value, "type") or "unknown")
    if kind == "text":
        return MCPContent("text", str(_field(value, "text") or ""))
    if kind in {"image", "audio"}:
        return MCPContent(
            kind,
            str(_field(value, "data") or ""),
            media_type=_field(value, "mime_type", "mimeType"),
            metadata=_mapping(_field(value, "meta", "_meta")) or {},
        )
    if kind == "resource":
        return MCPContent(
            "resource",
            _json_value(_field(value, "resource")),
            metadata=_mapping(_field(value, "meta", "_meta")) or {},
        )
    if kind == "resource_link":
        return MCPContent("resource_link", _json_value(value))
    return MCPContent(
        "unknown", None, metadata={"original_type": kind}
    )


def _field(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _mapping(value: Any) -> dict[str, Any] | None:
    converted = _json_value(value)
    return converted if isinstance(converted, dict) else None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _json_value(dump(by_alias=True, exclude_none=True))
    return str(value)
