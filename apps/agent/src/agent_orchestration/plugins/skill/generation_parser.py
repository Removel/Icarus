"""Strict parser for one generated SKILL.md document."""

import json
import re
from typing import Any

from pydantic import ValidationError

from apps.agent.src.agent_orchestration.plugins.skill.generation_models import (
    GeneratedSkill,
)


_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


class SkillGenerationParseError(ValueError):
    pass


class SkillGenerationParser:
    def parse(self, text: str) -> GeneratedSkill:
        document = self._extract_document(text)
        try:
            payload = json.loads(
                document,
                parse_constant=self._reject_non_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise SkillGenerationParseError(
                "Skill generation output is not valid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise SkillGenerationParseError(
                "Skill generation JSON must be an object"
            )
        try:
            return GeneratedSkill.model_validate(payload)
        except ValidationError as error:
            raise SkillGenerationParseError(
                "Skill generation output does not match the required schema"
            ) from error

    @staticmethod
    def _extract_document(text: str) -> str:
        if not isinstance(text, str):
            raise SkillGenerationParseError(
                "Skill generation output must be a string"
            )
        candidate = text.strip()
        if not candidate:
            raise SkillGenerationParseError(
                "Skill generation output cannot be empty"
            )
        if candidate.startswith("```"):
            match = _JSON_FENCE.fullmatch(candidate)
            if match is None:
                raise SkillGenerationParseError(
                    "Skill generation output must contain one complete fenced "
                    "JSON document"
                )
            candidate = match.group("body").strip()
            if not candidate:
                raise SkillGenerationParseError(
                    "Fenced Skill generation output cannot be empty"
                )
        return candidate

    @staticmethod
    def _reject_non_json_constant(value: str) -> Any:
        raise ValueError(f"invalid JSON constant: {value}")
