"""Session-scoped MCP connections and lazy Tool Catalog loading."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
import logging

from apps.agent.src.agent_orchestration.plugins.mcp.backend import (
    FastMCPClientBackend,
    MCPClientBackend,
)
from apps.agent.src.agent_orchestration.plugins.mcp.catalog import MCPToolCatalog
from apps.agent.src.agent_orchestration.plugins.mcp.config import MCPServerConfig
from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPToolDescriptor,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor


BackendFactory = Callable[
    [MCPServerConfig, Callable[[], None]], MCPClientBackend
]
ArgumentValidator = Callable[[MCPToolDescriptor, Mapping[str, object]], None]


@dataclass
class _ServerRuntime:
    config: MCPServerConfig
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    backend: MCPClientBackend | None = None
    catalog_loaded: bool = False
    catalog_stale: bool = False
    catalog_revision: int = 0
    last_error: str | None = None


class MCPClientManager:
    def __init__(
        self,
        servers: tuple[MCPServerConfig, ...],
        *,
        workspace_path: str,
        backend_factory: BackendFactory | None = None,
        argument_validator: ArgumentValidator | None = None,
        logger: logging.Logger | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.catalog = MCPToolCatalog()
        self._servers = {item.name: _ServerRuntime(item) for item in servers}
        self._backend_factory = backend_factory or self._create_backend
        self._argument_validator = argument_validator or _validate_arguments
        self._logger = logger or logging.getLogger("icarus.agent.mcp")
        self._redactor = redactor or Redactor()
        self._closed = False

    @property
    def server_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._servers))

    def mark_stale(self, server: str) -> None:
        if self._closed:
            return
        runtime = self._require_server(server)
        runtime.catalog_revision += 1
        runtime.catalog_stale = True

    async def ensure_catalog(self, server: str) -> None:
        runtime = self._require_server(server)
        if runtime.catalog_loaded and not runtime.catalog_stale:
            return
        async with runtime.lock:
            if runtime.catalog_loaded and not runtime.catalog_stale:
                return
            refresh_revision = runtime.catalog_revision
            backend = runtime.backend
            if backend is None:
                backend = self._backend_factory(
                    runtime.config, lambda: self.mark_stale(server)
                )
                runtime.backend = backend
            try:
                await backend.connect()
                tools = await backend.list_tools()
            except Exception as error:
                runtime.last_error = f"{type(error).__name__}: {error}"
                try:
                    await self._discard_backend(runtime, backend)
                except Exception as cleanup_error:
                    error.add_note(
                        "MCP Client cleanup also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise
            self.catalog.replace_server(server, tools)
            runtime.catalog_loaded = True
            runtime.catalog_stale = (
                runtime.catalog_revision != refresh_revision
            )
            runtime.last_error = None

    async def load_catalogs(
        self, server: str | None = None
    ) -> dict[str, str]:
        names = (server,) if server is not None else self.server_names
        if server is not None:
            self._require_server(server)
        results = await asyncio.gather(
            *(self.ensure_catalog(name) for name in names),
            return_exceptions=True,
        )
        errors = {}
        for name, result in zip(names, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                errors[name] = f"{type(result).__name__}: {result}"
        return errors

    async def list_tools(
        self, *, server: str | None, page: int, page_size: int
    ) -> tuple[tuple[MCPToolDescriptor, ...], int, dict[str, str]]:
        errors = await self.load_catalogs(server)
        tools, total = self.catalog.list(
            server=server, page=page, page_size=page_size,
            exclude_servers=frozenset(errors),
        )
        return tools, total, errors

    async def search_tools(
        self, *, query: str, server: str | None, limit: int
    ) -> tuple[tuple[MCPToolDescriptor, ...], dict[str, str]]:
        errors = await self.load_catalogs(server)
        return self.catalog.search(
            query, server=server, limit=limit,
            exclude_servers=frozenset(errors),
        ), errors

    async def call_tool(
        self, tool_ref: str, arguments: Mapping[str, object]
    ) -> MCPCallResult:
        server, separator, _ = tool_ref.partition("/")
        if not separator:
            raise ValueError("tool_ref is invalid")
        await self.ensure_catalog(server)
        descriptor = self.catalog.get(tool_ref)
        if descriptor is None:
            raise KeyError(f"MCP Tool is not found: {tool_ref}")
        self._argument_validator(descriptor, arguments)
        runtime = self._require_server(server)
        backend = runtime.backend
        if backend is None:
            raise RuntimeError(f"MCP server is not connected: {server}")
        try:
            return await backend.call_tool(descriptor.name, arguments)
        except Exception as error:
            runtime.last_error = f"{type(error).__name__}: {error}"
            try:
                await self._discard_backend(runtime, backend)
            except Exception as cleanup_error:
                error.add_note(
                    "MCP Client cleanup also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        results = await asyncio.gather(
            *(self._discard_backend(runtime) for runtime in self._servers.values()),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise RuntimeError(
                "MCP Client cleanup failed: "
                + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
            )

    def _create_backend(
        self, config: MCPServerConfig, tools_changed: Callable[[], None]
    ) -> MCPClientBackend:
        return FastMCPClientBackend(
            config,
            workspace_path=self.workspace_path,
            tools_changed=tools_changed,
            logger=self._logger.getChild(config.name),
            redactor=self._redactor,
        )

    def _require_server(self, server: str) -> _ServerRuntime:
        if self._closed:
            raise RuntimeError("MCP Client Manager is closed")
        try:
            return self._servers[server]
        except KeyError as error:
            raise KeyError(f"MCP Server is not configured: {server}") from error

    @staticmethod
    async def _discard_backend(
        runtime: _ServerRuntime,
        expected_backend: MCPClientBackend | None = None,
    ) -> None:
        backend = expected_backend or runtime.backend
        if backend is None:
            return
        if runtime.backend is backend:
            runtime.backend = None
            runtime.catalog_stale = True
        await backend.close()


def _validate_arguments(
    descriptor: MCPToolDescriptor, arguments: Mapping[str, object]
) -> None:
    try:
        from jsonschema import exceptions as jsonschema_exceptions
        from jsonschema.validators import validator_for
        from referencing import Registry
        from referencing.exceptions import Unresolvable
    except ImportError as error:
        raise RuntimeError(
            "JSON Schema validation support is not installed"
        ) from error
    schema = descriptor.as_dict()["input_schema"]
    try:
        validator_type = validator_for(schema)
        validator_type.check_schema(schema)
        errors = sorted(
            validator_type(
                schema, registry=Registry()
            ).iter_errors(dict(arguments)),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except jsonschema_exceptions.SchemaError as error:
        raise ValueError(
            f"MCP Tool has an invalid input schema: {descriptor.tool_ref}: {error.message}"
        ) from error
    except Unresolvable as error:
        raise ValueError(
            f"MCP Tool input schema cannot resolve external reference: {descriptor.tool_ref}"
        ) from error
    if not errors:
        return
    details = []
    for error in errors[:8]:
        path = "$." + ".".join(str(part) for part in error.absolute_path)
        details.append(
            f"{path.rstrip('.')}: failed {error.validator} validation"
        )
    raise ValueError(
        f"MCP Tool arguments are invalid: {descriptor.tool_ref}: "
        + "; ".join(details)
    )
