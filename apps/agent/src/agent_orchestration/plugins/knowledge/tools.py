"""The five fixed Agent-visible Knowledge tools."""

from __future__ import annotations

from typing import Any

from apps.agent.src.agent_orchestration.plugins.knowledge.plugin import KnowledgePlugin
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class _KnowledgeTool(BaseTool):
    def __init__(self, plugin: KnowledgePlugin) -> None:
        self.plugin = plugin

    @staticmethod
    def _keys(
        arguments: dict[str, Any],
        *,
        required: frozenset[str] = frozenset(),
        optional: frozenset[str] = frozenset(),
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
    def _string(arguments: dict[str, Any], name: str) -> str:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _bool(arguments: dict[str, Any], name: str, default: bool) -> bool:
        value = arguments.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value

    @staticmethod
    def _failure(operation: str, error: Exception) -> ToolExecutionResult:
        return ToolExecutionResult(False, error=f"knowledge {operation} failed: {error}")


class KnowledgeQueryTool(_KnowledgeTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "knowledge_query",
            "Ask the configured knowledge base a focused factual question.",
            {
                "type": "object",
                "properties": {"question": {"type": "string", "minLength": 1}},
                "required": ["question"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"question"}))
            return ToolExecutionResult(
                True, output=self.plugin.query(self._string(arguments, "question"))
            )
        except Exception as error:
            return self._failure("query", error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class KnowledgeListTool(_KnowledgeTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "knowledge_list",
            "List documents and generated wiki pages in the configured knowledge base.",
            {
                "type": "object",
                "properties": {
                    "page_num": {
                        "type": "integer", "minimum": 1, "default": 1
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": self.plugin.max_page_size,
                        "default": self.plugin.default_page_size,
                    },
                },
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(
                arguments, optional=frozenset({"page_num", "page_size"})
            )
            return ToolExecutionResult(
                True,
                output=self.plugin.list(
                    page_num=arguments.get("page_num", 1),
                    page_size=arguments.get(
                        "page_size", self.plugin.default_page_size
                    ),
                ),
            )
        except Exception as error:
            return self._failure("list", error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class KnowledgeReadTool(_KnowledgeTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "knowledge_read",
            "Read one wiki page path returned by knowledge_list.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"path"}))
            return ToolExecutionResult(
                True, output=self.plugin.read(self._string(arguments, "path"))
            )
        except Exception as error:
            return self._failure("read", error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class KnowledgeUploadTool(_KnowledgeTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "knowledge_upload",
            "Upload and compile one or more regular files inside the current workspace.",
            {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array", "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    }
                },
                "required": ["paths"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"paths"}))
            paths = arguments.get("paths")
            if not isinstance(paths, list) or not paths:
                raise ValueError("paths must be a non-empty array")
            if not all(isinstance(path, str) for path in paths):
                raise ValueError("paths must contain only strings")
            return ToolExecutionResult(
                True, output=self.plugin.upload(tuple(paths))
            )
        except Exception as error:
            return self._failure("upload", error)


class KnowledgeRecompileTool(_KnowledgeTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "knowledge_recompile",
            "Recompile one exact document or explicitly recompile all documents.",
            {
                "type": "object",
                "properties": {
                    "document": {"type": ["string", "null"], "minLength": 1},
                    "all_documents": {"type": "boolean", "default": False},
                    "refresh_schema": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(
                arguments,
                optional=frozenset({"document", "all_documents", "refresh_schema"}),
            )
            raw_document = arguments.get("document")
            document = None
            if raw_document is not None:
                document = self._string(arguments, "document")
            return ToolExecutionResult(
                True,
                output=self.plugin.recompile(
                    document=document,
                    all_documents=self._bool(arguments, "all_documents", False),
                    refresh_schema=self._bool(arguments, "refresh_schema", False),
                ),
            )
        except Exception as error:
            return self._failure("recompile", error)


def create_knowledge_tools(plugin: KnowledgePlugin) -> tuple[BaseTool, ...]:
    return (
        KnowledgeQueryTool(plugin),
        KnowledgeListTool(plugin),
        KnowledgeReadTool(plugin),
        KnowledgeUploadTool(plugin),
        KnowledgeRecompileTool(plugin),
    )
