"""Strict parser for Skill maintenance Agent output."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from apps.agent.src.agent_orchestration.plugins.skill.maintenance_models import (
    SkillMaintenancePlan,
)


_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


class SkillMaintenanceParseError(ValueError):
    """Raised when maintenance output cannot become a validated plan."""


def extract_json_document(text: str) -> str:
    """Extract one complete JSON document without prose recovery."""

    if not isinstance(text, str):
        raise SkillMaintenanceParseError(
            "maintenance output must be a string"
        )
    candidate = text.strip()
    if not candidate:
        raise SkillMaintenanceParseError("maintenance output cannot be empty")
    if candidate.startswith("```"):
        match = _JSON_FENCE.fullmatch(candidate)
        if match is None:
            raise SkillMaintenanceParseError(
                "maintenance output must contain one complete fenced JSON document"
            )
        candidate = match.group("body").strip()
        if not candidate:
            raise SkillMaintenanceParseError(
                "fenced maintenance output cannot be empty"
            )
    return candidate


def _reject_non_json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


class SkillMaintenanceParser:
    """Parse only pure JSON or a single fenced JSON document."""

    def parse(self, text: str) -> SkillMaintenancePlan:
        document = extract_json_document(text)
        try:
            payload = json.loads(
                document,
                parse_constant=_reject_non_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise SkillMaintenanceParseError(
                "maintenance output is not valid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise SkillMaintenanceParseError(
                "maintenance plan JSON must be an object"
            )
        try:
            return SkillMaintenancePlan.model_validate(payload)
        except ValidationError as error:
            raise SkillMaintenanceParseError(
                "maintenance plan does not match the required schema"
            ) from error


def parse_skill_maintenance_plan(text: str) -> SkillMaintenancePlan:
    """Convenience wrapper around :class:`SkillMaintenanceParser`."""

    return SkillMaintenanceParser().parse(text)
