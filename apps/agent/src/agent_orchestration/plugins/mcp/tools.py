"""The three fixed Agent-visible MCP tools."""

from __future__ import annotations

from typing import Any

from apps.agent.src.agent_orchestration.plugins.mcp.plugin import MCPPlugin
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
DEFAULT_SEARCH_LIMIT = 5
class _MCPTool(BaseTool):
    def __init__(
        self,
        plugin: MCPPlugin,
        *,
        default_page_size: int = DEFAULT_PAGE_SIZE,
        max_page_size: int = MAX_PAGE_SIZE,
    ) -> None:
        if not 1 <= default_page_size <= max_page_size:
            raise ValueError(
                "default_page_size must be between 1 and max_page_size"
            )
        self.plugin = plugin
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size

    @staticmethod
    def _keys(
        arguments: dict[str, Any],
        *,
        required: frozenset[str],
        optional: frozenset[str],
    ) -> None:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        missing = required - set(arguments)
        unknown = set(arguments) - required - optional
        if missing:
            raise ValueError("missing arguments: " + ", ".join(sorted(missing)))
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))

    @staticmethod
    def _optional_string(arguments: dict[str, Any], name: str) -> str | None:
        value = arguments.get(name)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _positive_int(
        arguments: dict[str, Any], name: str, default: int, maximum: int
    ) -> int:
        value = arguments.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        return value

    @staticmethod
    def _failure(error: Exception) -> ToolExecutionResult:
        return ToolExecutionResult(False, error=f"{type(error).__name__}: {error}")


class MCPToolList(_MCPTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "mcp_tool_list",
            "List configured MCP tools with their complete input schemas. "
            "Use pagination for large catalogs.",
            {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "minLength": 1},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {
                        "type": "integer", "minimum": 1,
                        "maximum": self.max_page_size,
                        "default": DEFAULT_SEARCH_LIMIT,
                    },
                },
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return self.plugin.list_tools(**values)
        except Exception as error:
            return self._failure(error)

    async def ainvoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return await self.plugin.alist_tools(**values)
        except Exception as error:
            return self._failure(error)

    def _values(self, arguments):
        self._keys(
            arguments, required=frozenset(),
            optional=frozenset({"server", "page", "page_size"}),
        )
        return {
            "server": self._optional_string(arguments, "server"),
            "page": self._positive_int(arguments, "page", 1, 1_000_000),
            "page_size": self._positive_int(
                arguments,
                "page_size",
                self.default_page_size,
                self.max_page_size,
            ),
        }

    def can_run_parallel(self, arguments):
        return True


class MCPToolSearch(_MCPTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "mcp_tool_search",
            "Search configured MCP tools by name and description. Returns "
            "complete input schemas for matching tools.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "server": {"type": "string", "minLength": 1},
                    "limit": {
                        "type": "integer", "minimum": 1,
                        "maximum": self.max_page_size,
                        "default": self.default_page_size,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return self.plugin.search_tools(**values)
        except Exception as error:
            return self._failure(error)

    async def ainvoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return await self.plugin.asearch_tools(**values)
        except Exception as error:
            return self._failure(error)

    def _values(self, arguments):
        self._keys(
            arguments, required=frozenset({"query"}),
            optional=frozenset({"server", "limit"}),
        )
        query = arguments["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        return {
            "query": query.strip(),
            "server": self._optional_string(arguments, "server"),
            "limit": self._positive_int(
                arguments,
                "limit",
                DEFAULT_SEARCH_LIMIT,
                self.max_page_size,
            ),
        }

    def can_run_parallel(self, arguments):
        return True


class MCPToolExecute(_MCPTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "mcp_tool_execute",
            "Execute an MCP tool found by mcp_tool_list or mcp_tool_search. "
            "Pass the returned tool_ref unchanged and put the target tool's "
            "parameters in arguments.",
            {
                "type": "object",
                "properties": {
                    "tool_ref": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object", "default": {}},
                },
                "required": ["tool_ref"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return self.plugin.execute_tool(
                **values, timeout_seconds=execution.get("timeout_seconds")
            )
        except Exception as error:
            return self._failure(error)

    async def ainvoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            values = self._values(arguments)
            return await self.plugin.aexecute_tool(
                **values, timeout_seconds=execution.get("timeout_seconds")
            )
        except Exception as error:
            return self._failure(error)

    def _values(self, arguments):
        self._keys(
            arguments, required=frozenset({"tool_ref"}),
            optional=frozenset({"arguments"}),
        )
        tool_ref = arguments["tool_ref"]
        if not isinstance(tool_ref, str) or not tool_ref.strip():
            raise ValueError("tool_ref must be a non-empty string")
        target_arguments = arguments.get("arguments", {})
        if not isinstance(target_arguments, dict):
            raise ValueError("arguments must be an object")
        return {"tool_ref": tool_ref, "arguments": target_arguments}


def create_mcp_tools(
    plugin: MCPPlugin,
    *,
    default_page_size: int = DEFAULT_PAGE_SIZE,
    max_page_size: int = MAX_PAGE_SIZE,
) -> tuple[BaseTool, ...]:
    options = {
        "default_page_size": default_page_size,
        "max_page_size": max_page_size,
    }
    return (
        MCPToolList(plugin, **options),
        MCPToolSearch(plugin, **options),
        MCPToolExecute(plugin, **options),
    )
