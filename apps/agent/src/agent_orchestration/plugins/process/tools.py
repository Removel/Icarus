"""The single Agent-visible background process Tool."""

from __future__ import annotations

from typing import Any

from apps.agent.src.agent_orchestration.plugins.process.manager import (
    ProcessOperationError,
)
from apps.agent.src.agent_orchestration.plugins.process.models import ProcessSnapshot
from apps.agent.src.agent_orchestration.plugins.process.plugin import ProcessPlugin
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class BackgroundProcessTool(BaseTool):
    _FIELDS = {
        "start": (frozenset({"action", "command"}), frozenset({"workdir"})),
        "list": (frozenset({"action"}), frozenset({"cursor", "page_size"})),
        "get": (frozenset({"action", "process_id"}), frozenset()),
        "logs": (
            frozenset({"action", "process_id"}),
            frozenset({"cursor", "limit_bytes"}),
        ),
        "stop": (frozenset({"action", "process_id"}), frozenset()),
    }

    def __init__(self, plugin: ProcessPlugin) -> None:
        self.plugin = plugin

    @property
    def definition(self) -> ToolDefinition:
        config = self.plugin.config
        return ToolDefinition(
            "background_process",
            "Start and manage Session-owned background commands such as development servers. "
            "A successful start means the OS process was created and is supervised; it does "
            "not mean the application is ready. Use get, list, or logs to observe it.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "list", "get", "logs", "stop"],
                    },
                    "command": {"type": "string", "minLength": 1},
                    "workdir": {"type": "string", "minLength": 1},
                    "process_id": {"type": "string", "minLength": 1},
                    "cursor": {"type": "string", "minLength": 1},
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": config.max_page_size,
                        "default": config.default_page_size,
                    },
                    "limit_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": config.max_log_page_bytes,
                        "default": config.default_log_page_bytes,
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self, arguments: dict[str, Any], *, task_id: str | None = None,
        timeout_seconds: float | None = None, **execution
    ) -> ToolExecutionResult:
        del execution
        try:
            values = self._validate(arguments, task_id=task_id)
            result = self.plugin.run_sync(
                lambda: self.plugin.execute(**values),
                timeout_seconds=timeout_seconds,
            )
            return ToolExecutionResult(True, self._serialize(result))
        except Exception as error:
            return self._failure(error)

    async def ainvoke(
        self, arguments: dict[str, Any], *, task_id: str | None = None,
        timeout_seconds: float | None = None, **execution
    ) -> ToolExecutionResult:
        del timeout_seconds, execution
        try:
            values = self._validate(arguments, task_id=task_id)
            result = await self.plugin.execute(**values)
            return ToolExecutionResult(True, self._serialize(result))
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        del arguments
        return False

    @classmethod
    def _validate(
        cls, arguments: dict[str, Any], *, task_id: str | None
    ) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        action = arguments.get("action")
        if not isinstance(action, str) or action not in cls._FIELDS:
            raise ValueError("action must be one of start, list, get, logs, stop")
        required, optional = cls._FIELDS[action]
        missing = required - set(arguments)
        unknown = set(arguments) - required - optional
        if missing:
            raise ValueError("missing arguments: " + ", ".join(sorted(missing)))
        if unknown:
            raise ValueError(
                f"arguments are not valid for {action}: "
                + ", ".join(sorted(unknown))
            )
        values = {name: arguments[name] for name in optional if name in arguments}
        values["action"] = action
        if action == "start":
            values["command"] = cls._string(arguments, "command")
            if "workdir" in values:
                values["workdir"] = cls._string(arguments, "workdir")
            values["origin_task_id"] = task_id
        elif action in {"get", "logs", "stop"}:
            values["process_id"] = cls._string(arguments, "process_id")
        if "cursor" in values:
            values["cursor"] = cls._string(arguments, "cursor")
        for name in ("page_size", "limit_bytes"):
            if name in values and (
                isinstance(values[name], bool) or not isinstance(values[name], int)
            ):
                raise ValueError(f"{name} must be an integer")
        return values

    @staticmethod
    def _string(arguments: dict[str, Any], name: str) -> str:
        value = arguments[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip() if name != "command" else value

    @classmethod
    def _serialize(cls, value: object) -> object:
        if isinstance(value, ProcessSnapshot):
            return value.to_dict()
        if isinstance(value, dict):
            result = dict(value)
            if "processes" in result:
                result["processes"] = [
                    cls._serialize(item) for item in result["processes"]
                ]
            return result
        return value

    @staticmethod
    def _failure(error: Exception) -> ToolExecutionResult:
        if isinstance(error, ProcessOperationError):
            message = str(error)
        elif isinstance(error, ValueError):
            message = f"invalid_arguments: {error}"
        else:
            message = f"internal_error: {type(error).__name__}"
        return ToolExecutionResult(False, error=message)
