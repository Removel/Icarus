"""Fixed search entry point for the non-resident tool surface."""

from __future__ import annotations

import json
from typing import Any

from icarus.core.models.messages import Message, ToolDefinition
from icarus.core.tools.base_tool import BaseTool
from icarus.core.tools.tool_surface import ToolSurfaceCatalog
from icarus.core.tools.types import ToolExecutionResult


class ToolSearchTool(BaseTool):
    def __init__(self, catalog: ToolSurfaceCatalog | None = None) -> None:
        self._catalog = catalog or ToolSurfaceCatalog()

    def bind_catalog(self, catalog: ToolSurfaceCatalog) -> None:
        self._catalog = catalog

    @property
    def definition(self) -> ToolDefinition:
        description = (
            "Search for a tool scope by exact plugin_id. Returns complete tool "
            "definitions; call returned tools with tool_execute(name, arguments). "
            "Use an empty query to list scopes. Available scopes:"
        )
        directory = self._catalog.directory()
        if directory:
            description += "\n" + "\n".join(directory)
        else:
            description += "\nNo tool scopes are registered."
        return ToolDefinition(
            "tool_search",
            description,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Exact plugin_id scope, or empty to list scopes.",
                        "default": "",
                    },
                },
                "additionalProperties": False,
            },
        )

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        try:
            query = self._query(arguments)
        except Exception as error:
            return ToolExecutionResult(
                False, error=f"{type(error).__name__}: {error}"
            )
        definitions = self._catalog.search(query) if query else ()
        if not definitions:
            return ToolExecutionResult(True, self._directory_output())
        output = [
            f"Loaded tools from scope '{query}'. "
            "Call them with tool_execute(name, arguments)."
        ]
        for definition in definitions:
            output.extend(
                [
                    "",
                    definition.name,
                    definition.description,
                    json.dumps(
                        definition.input_schema,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ]
            )
        return ToolExecutionResult(
            True,
            "\n".join(output),
            metadata={
                "tools_loaded": [definition.name for definition in definitions]
            },
        )

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        return self.invoke(arguments)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True

    def _directory_output(self) -> str:
        directory = self._catalog.directory()
        if not directory:
            return "No tool scopes are registered."
        return (
            "Available tool scopes. Call tool_search with an exact scope, then "
            "call returned tools with tool_execute(name, arguments).\n"
            + "\n".join(directory)
        )

    @staticmethod
    def _query(arguments: dict[str, Any]) -> str:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        unknown = set(arguments) - {"query"}
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
        query = arguments.get("query", "")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        return query.strip()
