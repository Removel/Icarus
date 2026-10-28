"""Dispatch loaded tools through the complete ToolExecutor chain."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from icarus.core.models.messages import Message, ToolCall, ToolDefinition
from icarus.core.tools.base_tool import BaseTool
from icarus.core.tools.tool_registry import ToolRegistry
from icarus.core.tools.types import ToolExecutionResult

if TYPE_CHECKING:
    from icarus.core.tools.tool_executor import BaseToolExecutor


class _ArgumentValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(
            "; ".join(
                f"{error['path']}: failed {error['validator']} validation; "
                f"expected {error['expected']!r}; actual {error['actual']!r}"
                for error in errors
            )
        )


class ToolExecuteTool(BaseTool):
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self._registry: ToolRegistry | None = None
        self._executor: BaseToolExecutor | None = None
        if registry is not None:
            self.bind_registry(registry)

    def bind_registry(self, registry: ToolRegistry) -> None:
        from icarus.core.tools.tool_executor import ToolExecutor

        self._registry = registry
        self._executor = ToolExecutor(registry)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            "tool_execute",
            "Execute a tool returned by tool_search. Pass its name unchanged "
            "and put the target tool's parameters in arguments as an object.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object", "default": {}},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        loaded_tools: list[str] | None = None,
        tool_executor: BaseToolExecutor | None = None,
        tool_call_id: str | None = None,
        execution_control: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        try:
            tool_call = self._prepare_call(
                arguments,
                loaded_tools=loaded_tools,
                tool_call_id=tool_call_id,
                execution_control=execution_control,
                timeout_seconds=timeout_seconds,
            )
        except _ArgumentValidationError as error:
            return ToolExecutionResult(
                False,
                output={"errors": error.errors},
                error=f"Tool arguments are invalid: {error}",
            )
        except Exception as error:
            return self._failure(error)
        executor = tool_executor or self._executor
        if executor is None:
            return self._failure(RuntimeError("Tool Registry is not bound"))
        try:
            return executor.execute(
                tool_call,
                task_id=task_id,
                run_id=run_id,
                step=step,
                task_messages=task_messages,
            )
        except Exception as error:
            return self._failure(error)

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        loaded_tools: list[str] | None = None,
        tool_executor: BaseToolExecutor | None = None,
        tool_call_id: str | None = None,
        execution_control: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        try:
            tool_call = self._prepare_call(
                arguments,
                loaded_tools=loaded_tools,
                tool_call_id=tool_call_id,
                execution_control=execution_control,
                timeout_seconds=timeout_seconds,
            )
        except _ArgumentValidationError as error:
            return ToolExecutionResult(
                False,
                output={"errors": error.errors},
                error=f"Tool arguments are invalid: {error}",
            )
        except Exception as error:
            return self._failure(error)
        executor = tool_executor or self._executor
        if executor is None:
            return self._failure(RuntimeError("Tool Registry is not bound"))
        try:
            return await executor.aexecute(
                tool_call,
                task_id=task_id,
                run_id=run_id,
                step=step,
                task_messages=task_messages,
            )
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True

    def _prepare_call(
        self,
        arguments: dict[str, Any],
        *,
        loaded_tools: list[str] | None,
        tool_call_id: str | None,
        execution_control: dict[str, Any] | None,
        timeout_seconds: float | None,
    ) -> ToolCall:
        name, target_arguments = self._values(arguments)
        if name not in (loaded_tools or []):
            raise ValueError(
                f"Tool is not loaded in this Run: {name}. "
                "Call tool_search('<scope>') for its scope first, then retry "
                "tool_execute."
            )
        if self._registry is None:
            raise RuntimeError("Tool Registry is not bound")
        target = self._registry.get(name)
        if target is None:
            raise ValueError(f"Tool is not registered: {name}")
        errors = _validation_errors(target.definition, target_arguments)
        if errors:
            raise _ArgumentValidationError(errors)
        target_arguments = deepcopy(target_arguments)
        if execution_control is not None:
            target_arguments["_execution"] = deepcopy(execution_control)
        elif timeout_seconds is not None:
            target_arguments["_execution"] = {
                "timeout_seconds": timeout_seconds
            }
        return ToolCall(tool_call_id or uuid4().hex, name, target_arguments)

    @staticmethod
    def _values(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        unknown = set(arguments) - {"name", "arguments"}
        if unknown:
            raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
        name = arguments.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        target_arguments = arguments.get("arguments", {})
        if not isinstance(target_arguments, dict):
            raise ValueError("arguments must be an object")
        return name.strip(), target_arguments

    @staticmethod
    def _failure(error: Exception) -> ToolExecutionResult:
        return ToolExecutionResult(False, error=f"{type(error).__name__}: {error}")


def _validation_errors(
    definition: ToolDefinition,
    arguments: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        from jsonschema import exceptions as jsonschema_exceptions
        from jsonschema.validators import validator_for
        from referencing import Registry
        from referencing.exceptions import Unresolvable
    except ImportError as error:
        raise RuntimeError("JSON Schema validation support is not installed") from error
    schema = definition.input_schema
    try:
        validator_type = validator_for(schema)
        validator_type.check_schema(schema)
        validation_errors = sorted(
            validator_type(schema, registry=Registry()).iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except jsonschema_exceptions.SchemaError as error:
        raise ValueError(
            f"Tool has an invalid input schema: {definition.name}: {error.message}"
        ) from error
    except Unresolvable as error:
        raise ValueError(
            f"Tool input schema cannot resolve external reference: {definition.name}"
        ) from error
    errors = []
    for validation_error in validation_errors[:8]:
        path = "$." + ".".join(str(part) for part in validation_error.absolute_path)
        errors.append(
            {
                "path": path.rstrip("."),
                "validator": validation_error.validator,
                "expected": deepcopy(
                    validation_error.schema.get(validation_error.validator)
                ),
                "actual": deepcopy(validation_error.instance),
                "message": validation_error.message,
            }
        )
    return errors
