"""Agent-visible read-only Blackboard Region tools."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from apps.agent.src.agent_orchestration.plugins.blackboard.regions import (
    RegionOutput,
    RegionStore,
)
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class _BlackboardTool(BaseTool):
    def __init__(self, store: RegionStore) -> None:
        self.store = store

    @staticmethod
    def _failure(error: Exception) -> ToolExecutionResult:
        return ToolExecutionResult(False, error=str(error))

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class BlackboardListTool(_BlackboardTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="blackboard_list",
            description=(
                "List current plugin-owned Blackboard Regions. This is read-only "
                "and never calls the underlying domain services."
            ),
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution: object) -> ToolExecutionResult:
        del execution
        try:
            if arguments != {}:
                raise ValueError("blackboard_list does not accept arguments")
            values = []
            current_input_id = self.store.current_input_id
            for snapshot in self.store.snapshots():
                output = snapshot.output
                values.append(
                    {
                        "region": snapshot.region,
                        "owner_plugin_id": snapshot.owner_plugin_id,
                        "lifetime": snapshot.lifetime,
                        "status": snapshot.state.status,
                        "summary": output.summary if output else None,
                        "refs": list(output.refs) if output else [],
                        "fresh_for_current_input": (
                            snapshot.input_id == current_input_id
                            if snapshot.lifetime == "input"
                            else None
                        ),
                        "updated_at": snapshot.updated_at.isoformat(),
                    }
                )
            return ToolExecutionResult(True, output={"regions": values})
        except Exception as error:
            return self._failure(error)


class BlackboardReadTool(_BlackboardTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="blackboard_read",
            description=(
                "Read bounded input, output, or state from one current Blackboard "
                "Region. Refs only filter data already present in the Region."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "minLength": 1},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["input", "output", "state"],
                        },
                        "uniqueItems": True,
                    },
                    "refs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "limit_chars": {"type": "integer", "minimum": 2},
                },
                "required": ["region"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution: object) -> ToolExecutionResult:
        del execution
        try:
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            unknown = set(arguments) - {"region", "sections", "refs", "limit_chars"}
            if unknown:
                raise ValueError("unknown arguments: " + ", ".join(sorted(unknown)))
            region = arguments.get("region")
            if not isinstance(region, str) or not region.strip():
                raise ValueError("region must be a non-empty string")
            definition = self.store.registry.get(region.strip())
            snapshot = self.store.get(region.strip())
            sections = arguments.get("sections", ["input", "output", "state"])
            if (
                not isinstance(sections, list)
                or not sections
                or any(item not in {"input", "output", "state"} for item in sections)
                or len(sections) != len(set(sections))
            ):
                raise ValueError("sections must contain unique input, output, or state values")
            refs = arguments.get("refs")
            if refs is not None and (
                not isinstance(refs, list)
                or any(not isinstance(item, str) or not item.strip() for item in refs)
                or len(refs) != len(set(refs))
            ):
                raise ValueError("refs must contain unique non-empty strings")
            limit = arguments.get("limit_chars", definition.max_data_chars)
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 2:
                raise ValueError("limit_chars must be an integer of at least 2")
            limit = min(limit, definition.max_data_chars)
            snapshot_value = snapshot.as_dict()
            selected: dict[str, Any] = {}
            for section in sections:
                value = deepcopy(snapshot_value[section])
                if section == "output" and value is not None and refs is not None:
                    value = _filter_output(value, frozenset(refs))
                if section in {"input", "output"} and value is not None:
                    value["data"], truncated = _bounded_data(value.get("data", {}), limit)
                else:
                    truncated = False
                selected[section] = value
                selected.setdefault("_truncated", False)
                selected["_truncated"] = selected["_truncated"] or truncated
            truncated = selected.pop("_truncated")
            return ToolExecutionResult(
                True,
                output={
                    "region": snapshot.region,
                    "owner_plugin_id": snapshot.owner_plugin_id,
                    "lifetime": snapshot.lifetime,
                    "input_id": snapshot.input_id,
                    "fresh_for_current_input": (
                        snapshot.input_id == self.store.current_input_id
                        if snapshot.lifetime == "input"
                        else None
                    ),
                    "sections": selected,
                    "updated_at": snapshot.updated_at.isoformat(),
                    "truncated": truncated,
                },
            )
        except Exception as error:
            return self._failure(error)


def create_blackboard_tools(store: RegionStore) -> tuple[BaseTool, ...]:
    return BlackboardListTool(store), BlackboardReadTool(store)


def _filter_output(value: dict[str, Any], refs: frozenset[str]) -> dict[str, Any]:
    value["refs"] = [item for item in value.get("refs", []) if item in refs]
    data = value.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data["items"] = [
            item
            for item in data["items"]
            if isinstance(item, dict) and item.get("ref") in refs
        ]
    return value


def _bounded_data(value: object, limit: int) -> tuple[object, bool]:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= limit:
        return value, False
    remaining = [limit]
    result = _truncate(value, remaining)
    while (
        len(
            json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        > limit
        and isinstance(result, dict)
        and result
    ):
        result.pop(sorted(result)[-1])
    return result, True


def _truncate(value: object, remaining: list[int]) -> object:
    if isinstance(value, str):
        allowed = max(0, remaining[0])
        result = value[:allowed]
        remaining[0] -= len(result)
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            if remaining[0] <= 0:
                break
            result.append(_truncate(item, remaining))
        return result
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            if remaining[0] <= 0:
                break
            result[key] = _truncate(value[key], remaining)
        return result
    return value
