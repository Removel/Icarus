"""The six fixed Agent-visible Memory tools."""

from __future__ import annotations

from typing import Any

from agent_app.plugins.memory.models import MemoryScope
from agent_app.plugins.memory.plugin import MemoryPlugin
from icarus.core.tools import BaseTool, ToolExecutionResult
from icarus.core.models.messages import Message, ToolDefinition


class _MemoryTool(BaseTool):
    def __init__(self, plugin: MemoryPlugin) -> None:
        self.plugin = plugin

    @staticmethod
    def _keys(
        arguments: dict[str, Any], *, required: frozenset[str],
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
    def _failure(error: Exception) -> ToolExecutionResult:
        return ToolExecutionResult(False, error=f"memory operation failed: {error}")


class MemoryRecallTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_recall",
            (
                "Search long-term memory when automatic recall is insufficient. "
                "An empty result does not prove that no memory exists. "
                "Usually only `query` is needed."
            ),
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Search text; use the user's wording or 2–5 key terms."
                        ),
                    },
                    "is_workspace": {
                        "type": ["boolean", "null"],
                        "default": None,
                        "description": (
                            "Scope: omit for both; true for project (workspace) "
                            "only; false for global only."
                        ),
                    },
                    "include_stopped": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Include memories stopped from default recall; "
                            "default false; usually omit."
                        ),
                    },
                    "top_k": {
                        "type": "integer", "minimum": 1, "maximum": 20,
                        "default": self.plugin.top_k,
                        "description": "Max results; default from config; usually omit.",
                    },
                    "threshold": {
                        "type": "number", "minimum": 0, "maximum": 1,
                        "default": self.plugin.threshold,
                        "description": (
                            "Min relevance 0–1; default from config; usually omit."
                        ),
                    },
                    "max_context_chars": {
                        "type": "integer", "minimum": 100, "maximum": 24000,
                        "default": self.plugin.max_context_chars,
                        "description": (
                            "Result size budget; default from config; usually omit."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        try:
            self._keys(arguments, required=frozenset({"query"}), optional=frozenset({"is_workspace", "include_stopped", "top_k", "threshold", "max_context_chars"}))
            scope_value = arguments.get("is_workspace")
            if scope_value is not None and not isinstance(scope_value, bool):
                raise ValueError("is_workspace must be a boolean or null")
            top_k = arguments.get("top_k", self.plugin.top_k)
            threshold = arguments.get("threshold", self.plugin.threshold)
            max_context_chars = arguments.get(
                "max_context_chars", self.plugin.max_context_chars
            )
            if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
                raise ValueError("top_k must be an integer from 1 to 20")
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ValueError("threshold must be a number from 0 to 1")
            if (
                isinstance(max_context_chars, bool)
                or not isinstance(max_context_chars, int)
                or not 100 <= max_context_chars <= 24000
            ):
                raise ValueError("max_context_chars must be an integer from 100 to 24000")
            scope: MemoryScope | None = (
                None if scope_value is None else ("workspace" if scope_value else "global")
            )
            return ToolExecutionResult(True, output=self.plugin.recall(
                self._string(arguments, "query"), scope=scope,
                include_stopped=self._bool(arguments, "include_stopped", False),
                top_k=top_k, threshold=float(threshold),
                max_context_chars=max_context_chars,
                task_id=execution.get("task_id"),
            ))
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class MemoryReadTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_read",
            "Read one owned memory by ref. Set include_history=true to see its change log.",
            {
                "type": "object",
                "properties": {
                    "ref": _ref_schema(),
                    "include_history": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include the memory's change history; default false.",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(
                arguments, required=frozenset({"ref"}),
                optional=frozenset({"include_history"}),
            )
            ref = self._string(arguments, "ref")
            if not self._bool(arguments, "include_history", False):
                return ToolExecutionResult(True, output=self.plugin.get(ref))
            history = self.plugin.history(ref)
            return ToolExecutionResult(True, output={
                "ref": ref,
                "memory": self.plugin.get(ref),
                "history": history["history"],
            })
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class MemoryRememberTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_remember",
            (
                "Store a stable fact, preference, agreement, decision, or project "
                "event worth keeping across conversations. Pass the user's original "
                "wording; do not summarize or paraphrase."
            ),
            {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Original user wording or the exact relevant passage; "
                            "do not summarize or rewrite."
                        ),
                    },
                    "is_workspace": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "true = project (workspace) level; false or omitted = "
                            "global level (default)."
                        ),
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, arguments, *, task_id=None, run_id=None, step=None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, step, task_messages
        try:
            self._keys(
                arguments, required=frozenset({"content"}),
                optional=frozenset({"is_workspace"}),
            )
            is_workspace = self._bool(arguments, "is_workspace", False)
            return ToolExecutionResult(True, output=self.plugin.remember(
                self._string(arguments, "content"),
                scope="workspace" if is_workspace else "global",
                run_id=run_id, session_id=self.plugin.session_id,
            ))
        except Exception as error:
            return self._failure(error)


class MemoryCorrectTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_correct",
            (
                "Replace the text of one owned memory. Find it with memory_recall "
                "first and copy its `ref`."
            ),
            {
                "type": "object",
                "properties": {
                    "ref": _ref_schema(),
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "The complete replacement text; not a diff or an instruction."
                        ),
                    },
                },
                "required": ["ref", "content"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"ref", "content"}))
            return ToolExecutionResult(True, output=self.plugin.correct(
                self._string(arguments, "ref"), self._string(arguments, "content")
            ))
        except Exception as error:
            return self._failure(error)


class MemorySetRecallTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_set_recall",
            "Stop or restore default recall for one owned memory without deleting it.",
            {
                "type": "object",
                "properties": {
                    "ref": _ref_schema(),
                    "enabled": {
                        "type": "boolean",
                        "description": (
                            "false = stop this memory from appearing in default recall "
                            "but keep it; true = restore it to default recall."
                        ),
                    },
                },
                "required": ["ref", "enabled"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"ref", "enabled"}))
            ref = self._string(arguments, "ref")
            enabled = self._bool(arguments, "enabled", False)
            method = self.plugin.restore_reference if enabled else self.plugin.stop_reference
            return ToolExecutionResult(True, output=method(ref))
        except Exception as error:
            return self._failure(error)


class _RefMutationTool(_MemoryTool):
    tool_name: str
    description: str
    method_name: str

    @property
    def definition(self) -> ToolDefinition:
        return _ref_definition(self.tool_name, self.description)

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"ref"}))
            return ToolExecutionResult(True, output=getattr(self.plugin, self.method_name)(self._string(arguments, "ref")))
        except Exception as error:
            return self._failure(error)


class MemoryDeleteTool(_RefMutationTool):
    tool_name = "memory_delete"
    description = (
        "Delete one owned active memory. Use only when the user explicitly asks "
        "to forget or delete it. History is retained; this is not a purge."
    )
    method_name = "delete"


def create_memory_tools(plugin: MemoryPlugin) -> tuple[BaseTool, ...]:
    return (
        MemoryRecallTool(plugin), MemoryReadTool(plugin),
        MemoryRememberTool(plugin), MemoryCorrectTool(plugin),
        MemorySetRecallTool(plugin), MemoryDeleteTool(plugin),
    )


def _ref_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": "^memory:.+",
        "description": (
            "Reference from a memory_recall result, copied exactly, "
            "in the form memory:<id>."
        ),
    }


def _ref_definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name, description,
        {
            "type": "object",
            "properties": {"ref": _ref_schema()},
            "required": ["ref"],
            "additionalProperties": False,
        },
    )
