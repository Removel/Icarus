"""Validated structured plans produced by the Skill maintenance Agent."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SkillMaintenanceAction = Literal[
    "create",
    "update",
    "merge",
    "delete",
    "no_op",
]

SAFE_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CONTENT_ACTIONS = frozenset({"create", "update", "merge"})


def is_safe_skill_name(name: str) -> bool:
    """Return whether *name* is a normalized, traversal-safe directory name."""

    return SAFE_SKILL_NAME_PATTERN.fullmatch(name) is not None


class SkillMaintenanceOperation(BaseModel):
    """One proposed Workspace Skill maintenance operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: SkillMaintenanceAction
    target_name: str | None = None
    source_names: tuple[str, ...] = ()
    content: str | None = None
    reason: str = Field(min_length=1)

    @field_validator("target_name")
    @classmethod
    def validate_target_name(cls, value: str | None) -> str | None:
        if value is not None and not is_safe_skill_name(value):
            raise ValueError(
                "target_name must be a normalized safe Skill name"
            )
        return value

    @field_validator("source_names")
    @classmethod
    def validate_source_names(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        unsafe = [name for name in value if not is_safe_skill_name(name)]
        if unsafe:
            raise ValueError(
                "source_names must contain only normalized safe Skill names"
            )
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        reason = value.strip()
        if not reason:
            raise ValueError("reason cannot be blank")
        return reason

    @model_validator(mode="after")
    def validate_action_fields(self) -> "SkillMaintenanceOperation":
        if self.action == "no_op":
            if (
                self.target_name is not None
                or self.source_names
                or self.content is not None
            ):
                raise ValueError(
                    "no_op cannot include target_name, source_names, or content"
                )
            return self

        if self.target_name is None:
            raise ValueError(f"{self.action} requires target_name")

        if self.action in _CONTENT_ACTIONS:
            if self.content is None or not self.content.strip():
                raise ValueError(
                    f"{self.action} requires complete non-blank SKILL.md content"
                )
        elif self.action == "delete" and self.content is not None:
            raise ValueError("delete cannot include content")

        if self.action == "merge":
            if len(self.source_names) < 2:
                raise ValueError("merge requires at least two source_names")
            if len(set(self.source_names)) != len(self.source_names):
                raise ValueError(
                    "merge requires at least two distinct source_names"
                )
        elif self.source_names:
            raise ValueError(
                f"{self.action} cannot include source_names"
            )

        return self


class SkillMaintenancePlan(BaseModel):
    """A bounded, immutable, all-or-nothing validated maintenance plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: tuple[SkillMaintenanceOperation, ...] = Field(
        min_length=1,
        max_length=10,
    )

    @model_validator(mode="after")
    def validate_operations(self) -> "SkillMaintenancePlan":
        no_op_count = sum(
            operation.action == "no_op" for operation in self.operations
        )
        if no_op_count and len(self.operations) != 1:
            raise ValueError("no_op must be the only operation in a plan")

        touched_names: set[str] = set()
        for operation in self.operations:
            if operation.action == "no_op":
                continue
            operation_names = {operation.target_name}
            if operation.action == "merge":
                operation_names.update(operation.source_names)
            overlap = touched_names.intersection(operation_names)
            if overlap:
                raise ValueError(
                    "a plan cannot write or delete the same Skill more than "
                    f"once: {', '.join(sorted(overlap))}"
                )
            touched_names.update(operation_names)

        return self
