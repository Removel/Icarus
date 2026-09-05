"""Session-scoped MCP Client Plugin."""

from __future__ import annotations

import asyncio

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.mcp.async_bridge import MCPAsyncBridge
from apps.agent.src.agent_orchestration.plugins.mcp.client_manager import (
    MCPClientManager,
)
from apps.agent.src.agent_orchestration.plugins.mcp.result_converter import (
    MCPResultConverter,
)
from apps.agent.src.agent_orchestration.plugins.persistence.redactor import Redactor
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult


class MCPPlugin(BasePlugin):
    def __init__(
        self,
        plugin_id: str,
        *,
        manager: MCPClientManager,
        bridge: MCPAsyncBridge | None = None,
        result_converter: MCPResultConverter | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.manager = manager
        self.bridge = bridge or MCPAsyncBridge()
        self.result_converter = result_converter or MCPResultConverter()
        self.redactor = redactor or Redactor()
        self._accepting = False

    async def start(self) -> None:
        self._accepting = True

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        del source_plugin_id, event

    async def quiesce(self) -> None:
        # UserInputPlugin stops new Tasks while AgentPlugin cancels or drains
        # accepted Runs. Keep their in-flight Tool calls usable until stop().
        pass

    async def stop(self) -> None:
        self._accepting = False
        if not self.bridge.is_running:
            return
        try:
            await self.bridge.arun(self.manager.close)
        finally:
            await asyncio.to_thread(self.bridge.stop)

    def list_tools(
        self, *, server: str | None, page: int, page_size: int
    ) -> ToolExecutionResult:
        return self._run_sync(
            lambda: self.manager.list_tools(
                server=server, page=page, page_size=page_size
            ),
            operation="list MCP tools",
            transform=lambda value: _list_output(
                *value, page, page_size, requested_server=server,
                server_count=len(self.manager.server_names),
                redactor=self.redactor,
            ),
        )

    async def alist_tools(
        self, *, server: str | None, page: int, page_size: int
    ) -> ToolExecutionResult:
        return await self._run_async(
            lambda: self.manager.list_tools(
                server=server, page=page, page_size=page_size
            ),
            operation="list MCP tools",
            transform=lambda value: _list_output(
                *value, page, page_size, requested_server=server,
                server_count=len(self.manager.server_names),
                redactor=self.redactor,
            ),
        )

    def search_tools(
        self, *, query: str, server: str | None, limit: int
    ) -> ToolExecutionResult:
        return self._run_sync(
            lambda: self.manager.search_tools(
                query=query, server=server, limit=limit
            ),
            operation="search MCP tools",
            transform=lambda value: _search_output(
                query, *value, requested_server=server,
                server_count=len(self.manager.server_names),
                redactor=self.redactor,
            ),
        )

    async def asearch_tools(
        self, *, query: str, server: str | None, limit: int
    ) -> ToolExecutionResult:
        return await self._run_async(
            lambda: self.manager.search_tools(
                query=query, server=server, limit=limit
            ),
            operation="search MCP tools",
            transform=lambda value: _search_output(
                query, *value, requested_server=server,
                server_count=len(self.manager.server_names),
                redactor=self.redactor,
            ),
        )

    def execute_tool(
        self, *, tool_ref: str, arguments: dict[str, object]
    ) -> ToolExecutionResult:
        return self._run_sync(
            lambda: self.manager.call_tool(tool_ref, arguments),
            operation=f"execute MCP Tool {tool_ref}",
            transform=self.result_converter.convert,
        )

    async def aexecute_tool(
        self, *, tool_ref: str, arguments: dict[str, object]
    ) -> ToolExecutionResult:
        return await self._run_async(
            lambda: self.manager.call_tool(tool_ref, arguments),
            operation=f"execute MCP Tool {tool_ref}",
            transform=self.result_converter.convert,
        )

    def _run_sync(self, callable_, *, operation: str, transform):
        if not self._accepting:
            return ToolExecutionResult(False, error="MCP Plugin is not running")
        try:
            self.bridge.start()
            converted = transform(self.bridge.run(callable_))
            return (
                converted
                if isinstance(converted, ToolExecutionResult)
                else ToolExecutionResult(True, converted)
            )
        except Exception as error:
            return ToolExecutionResult(
                False, error=f"{operation} failed: {self._safe_error(error)}"
            )

    async def _run_async(self, callable_, *, operation: str, transform):
        if not self._accepting:
            return ToolExecutionResult(False, error="MCP Plugin is not running")
        try:
            await asyncio.to_thread(self.bridge.start)
            converted = transform(await self.bridge.arun(callable_))
            return converted if isinstance(converted, ToolExecutionResult) else ToolExecutionResult(True, converted)
        except Exception as error:
            return ToolExecutionResult(
                False, error=f"{operation} failed: {self._safe_error(error)}"
            )

    def _safe_error(self, error: Exception) -> str:
        return str(
            self.redactor.redact(
                {"error": f"{type(error).__name__}: {error}"}
            )["error"]
        )


def _list_output(
    tools, total, errors, page, page_size, *, requested_server, server_count,
    redactor,
):
    errors = redactor.redact(errors)
    output = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": page * page_size < total,
        "tools": [tool.as_dict() for tool in tools],
        "server_errors": errors,
    }
    return _catalog_result(
        output, errors, requested_server=requested_server,
        server_count=server_count,
    )


def _search_output(
    query, tools, errors, *, requested_server, server_count, redactor
):
    errors = redactor.redact(errors)
    output = {
        "query": query,
        "matches": [tool.as_dict() for tool in tools],
        "server_errors": errors,
    }
    return _catalog_result(
        output, errors, requested_server=requested_server,
        server_count=server_count,
    )


def _catalog_result(output, errors, *, requested_server, server_count):
    del requested_server, server_count
    if errors:
        return ToolExecutionResult(
            success=False,
            output=output,
            error="; ".join(
                f"{server}: {message}"
                for server, message in sorted(errors.items())
            ),
        )
    return output
