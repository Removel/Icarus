"""The eight fixed Agent-visible Memory tools."""

from __future__ import annotations

from typing import Any

from apps.agent.src.agent_orchestration.plugins.memory.models import MemoryScope
from apps.agent.src.agent_orchestration.plugins.memory.plugin import MemoryPlugin
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolDefinition


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
            "Explicitly search long-term memory when automatic recall is insufficient.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "is_workspace": {"type": ["boolean", "null"], "default": None},
                    "include_stopped": {"type": "boolean", "default": False},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": self.plugin.top_k},
                    "threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": self.plugin.threshold},
                    "max_context_chars": {"type": "integer", "minimum": 100, "maximum": 24000, "default": self.plugin.max_context_chars},
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


class MemoryGetTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return _ref_definition("memory_get", "Read one owned memory by ref.")

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"ref"}))
            return ToolExecutionResult(True, output=self.plugin.get(self._string(arguments, "ref")))
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class MemoryHistoryTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return _ref_definition("memory_history", "Read the audit history for one owned memory.")

    def invoke(self, arguments, **execution) -> ToolExecutionResult:
        del execution
        try:
            self._keys(arguments, required=frozenset({"ref"}))
            return ToolExecutionResult(True, output=self.plugin.history(self._string(arguments, "ref")))
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class MemoryRememberTool(_MemoryTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "memory_remember",
            "Store an explicit user fact or preference in long-term memory.",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1},
                    "is_workspace": {"type": "boolean"},
                },
                "required": ["content", "is_workspace"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, arguments, *, task_id=None, run_id=None, step=None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, step, task_messages
        try:
            self._keys(arguments, required=frozenset({"content", "is_workspace"}))
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
            "memory_correct", "Replace the text of one owned memory.",
            {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "pattern": "^memory:.+"},
                    "content": {"type": "string", "minLength": 1},
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


class MemoryStopReferenceTool(_RefMutationTool):
    tool_name = "memory_stop_reference"
    description = "Stop one memory from appearing in default recall without deleting it."
    method_name = "stop_reference"


class MemoryRestoreReferenceTool(_RefMutationTool):
    tool_name = "memory_restore_reference"
    description = "Restore one stopped memory to default recall."
    method_name = "restore_reference"


class MemoryDeleteTool(_RefMutationTool):
    tool_name = "memory_delete"
    description = "Delete one owned active memory. This is not a history purge."
    method_name = "delete"


def create_memory_tools(plugin: MemoryPlugin) -> tuple[BaseTool, ...]:
    return (
        MemoryRecallTool(plugin), MemoryGetTool(plugin), MemoryHistoryTool(plugin),
        MemoryRememberTool(plugin), MemoryCorrectTool(plugin),
        MemoryStopReferenceTool(plugin), MemoryRestoreReferenceTool(plugin),
        MemoryDeleteTool(plugin),
    )


def _ref_definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name, description,
        {
            "type": "object",
            "properties": {"ref": {"type": "string", "pattern": "^memory:.+"}},
            "required": ["ref"],
            "additionalProperties": False,
        },
    )
