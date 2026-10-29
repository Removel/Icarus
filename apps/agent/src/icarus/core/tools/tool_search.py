"""Fixed search entry point for the non-resident tool surface."""

from __future__ import annotations

import json
from dataclasses import replace
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
            "Use optional name for an exact tool in that scope. Only complete returned "
            "definitions are loaded. If truncated, search one name or request more budget; "
            "reading a saved result does not load tools. Use an empty query to list scopes. Available scopes:"
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
                    "name": {"type": "string", "minLength": 1,
                             "description": "Optional exact tool name within this scope."},
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
            name = arguments.get("name")
            if name is not None and (not isinstance(name, str) or not name.strip()):
                raise ValueError("name must be a non-empty string")
        except Exception as error:
            return ToolExecutionResult(
                False, error=f"{type(error).__name__}: {error}"
            )
        definitions = self._catalog.search(query) if query else ()
        if name is not None:
            definitions = tuple(item for item in definitions if item.name == name.strip())
        if not definitions:
            return ToolExecutionResult(True, self._directory_output())
        return ToolExecutionResult(True, {
            "scope": query,
            "tools": [{"name": item.name, "description": item.description,
                       "input_schema": item.input_schema} for item in definitions],
            "usage": "Call complete definitions with tool_execute(name, arguments). "
                     "If truncated, search an exact name or request more budget; saved files do not load tools.",
        }, metadata={"tools_loaded": [item.name for item in definitions]})

    def finalize_loaded_tools(self, result: ToolExecutionResult) -> ToolExecutionResult:
        loaded: list[str] = []
        output = result.output
        candidates = result.metadata.get("tools_loaded", [])
        if (result.success and isinstance(output, dict)
                and isinstance(output.get("scope"), str)
                and isinstance(output.get("tools"), list) and isinstance(candidates, list)):
            definitions = {item.name: {
                "name": item.name, "description": item.description,
                "input_schema": item.input_schema,
            } for item in self._catalog.search(output["scope"])}
            for name in candidates:
                if not isinstance(name, str) or name in loaded or name not in definitions:
                    continue
                expected = json.dumps(definitions[name], ensure_ascii=False, sort_keys=True)
                if any(isinstance(item, dict) and json.dumps(item, ensure_ascii=False, sort_keys=True) == expected
                       for item in output["tools"]):
                    loaded.append(name)
        return replace(result, metadata={**result.metadata, "tools_loaded": loaded})

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
        unknown = set(arguments) - {"query", "name"}
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
        query = arguments.get("query", "")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        return query.strip()
