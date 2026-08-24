"""The five Agent-visible tools exposed by SkillPlugin."""

from typing import Any

from apps.agent.src.agent_orchestration.plugins.skill.plugin import (
    SkillOperationError,
    SkillPlugin,
)
from apps.agent.src.agent_orchestration.tools import BaseTool, ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolDefinition


class _SkillTool(BaseTool):
    def __init__(self, plugin: SkillPlugin) -> None:
        self.plugin = plugin

    @staticmethod
    def _failure(error: Exception) -> ToolExecutionResult:
        if isinstance(error, SkillOperationError):
            return ToolExecutionResult(
                success=False, error=f"{error.code}: {error}"
            )
        return ToolExecutionResult(success=False, error=str(error))

    @staticmethod
    def _validate_keys(
        arguments: dict[str, Any],
        *,
        required: frozenset[str],
        optional: frozenset[str] = frozenset(),
    ) -> None:
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        keys = set(arguments)
        missing = required - keys
        unknown = keys - required - optional
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


class SkillsListTool(_SkillTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skills_list",
            description=(
                "Browse the lightweight Skill catalog only when you need to "
                "inspect available workflows. Use read on a returned path before following a Skill."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "global", "workspace"],
                        "default": "all",
                    }
                },
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution: object) -> ToolExecutionResult:
        del execution
        try:
            self._validate_keys(arguments, required=frozenset(), optional=frozenset({"scope"}))
            scope = arguments.get("scope", "all")
            if scope not in ("all", "global", "workspace"):
                raise ValueError("scope must be all, global, or workspace")
            return ToolExecutionResult(
                success=True,
                output={"skills": self.plugin.list_skills(scope)},
            )
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class SkillSearchTool(_SkillTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_search",
            description=(
                "Search for a specialized workflow or domain Skill that may help the current task. "
                "Use read on a returned path before following it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 8,
                    }
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution: object) -> ToolExecutionResult:
        del execution
        try:
            self._validate_keys(arguments, required=frozenset({"keywords"}))
            keywords = arguments["keywords"]
            if not isinstance(keywords, list):
                raise ValueError("keywords must be an array")
            return ToolExecutionResult(success=True, output={"skills": self.plugin.search(keywords)})
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


class SkillProduceTool(_SkillTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_produce",
            description=(
                "Create a new reusable Skill only when the current work established a concrete repeatable method. "
                "This starts a background Job; do not call it by default every turn."
            ),
            input_schema=_write_schema(include_scope=True),
        )

    def invoke(
        self, arguments: dict[str, Any], *, task_id=None, run_id=None, step=None,
        task_messages: tuple[Message, ...] = ()
    ) -> ToolExecutionResult:
        try:
            self._validate_keys(arguments, required=frozenset({"name", "scope", "instructions"}))
            scope = arguments["scope"]
            if scope not in ("global", "workspace"):
                raise ValueError("scope must be global or workspace")
            output = self.plugin.produce(
                name=self._string(arguments, "name"),
                scope=scope,
                instructions=self._string(arguments, "instructions"),
                task_id=task_id, run_id=run_id, step=step, task_messages=task_messages,
            )
            return ToolExecutionResult(success=True, output=output)
        except Exception as error:
            return self._failure(error)


class SkillEvolveTool(_SkillTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_evolve",
            description=(
                "Evolve an existing Skill only when the current work provides a concrete reusable improvement. "
                "This starts a background Job; do not call it by default every turn."
            ),
            input_schema=_write_schema(include_scope=False),
        )

    def invoke(
        self, arguments: dict[str, Any], *, task_id=None, run_id=None, step=None,
        task_messages: tuple[Message, ...] = ()
    ) -> ToolExecutionResult:
        try:
            self._validate_keys(arguments, required=frozenset({"name", "instructions"}))
            output = self.plugin.evolve(
                name=self._string(arguments, "name"),
                instructions=self._string(arguments, "instructions"),
                task_id=task_id, run_id=run_id, step=step, task_messages=task_messages,
            )
            return ToolExecutionResult(success=True, output=output)
        except Exception as error:
            return self._failure(error)


class SkillJobStatusTool(_SkillTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="skill_job_status",
            description="Query the current or final state of a Skill produce/evolve Job.",
            input_schema={
                "type": "object",
                "properties": {"job_id": {"type": "string", "minLength": 1}},
                "required": ["job_id"],
                "additionalProperties": False,
            },
        )

    def invoke(self, arguments: dict[str, Any], **execution: object) -> ToolExecutionResult:
        del execution
        try:
            self._validate_keys(arguments, required=frozenset({"job_id"}))
            return ToolExecutionResult(
                success=True, output=self.plugin.job_status(self._string(arguments, "job_id"))
            )
        except Exception as error:
            return self._failure(error)

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return True


def create_skill_tools(plugin: SkillPlugin) -> tuple[BaseTool, ...]:
    return (
        SkillsListTool(plugin),
        SkillSearchTool(plugin),
        SkillProduceTool(plugin),
        SkillEvolveTool(plugin),
        SkillJobStatusTool(plugin),
    )


def _write_schema(*, include_scope: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "name": {"type": "string", "minLength": 1},
        "instructions": {"type": "string", "minLength": 1},
    }
    required = ["name", "instructions"]
    if include_scope:
        properties["scope"] = {"type": "string", "enum": ["workspace", "global"]}
        required.insert(1, "scope")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
