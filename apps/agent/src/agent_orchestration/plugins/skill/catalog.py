"""Deterministic Skill listing and simple keyword search."""

import re
from collections.abc import Sequence
from typing import Literal

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
    normalize_skill_name,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner


CatalogScope = Literal["all", "global", "workspace"]
_SEARCH_SEPARATOR = re.compile(r"[\s_-]+")
_SEARCH_LIMIT = 10


def normalize_search_text(value: str) -> str:
    """Normalize only the separators supported by the search contract."""
    return _SEARCH_SEPARATOR.sub(" ", value.casefold()).strip()


class SkillCatalog:
    def __init__(self, scanner: SkillScanner) -> None:
        self._scanner = scanner

    def list_skills(
        self, scope: CatalogScope = "all"
    ) -> list[SkillDefinition]:
        if scope == "all":
            return self._scanner.scan()
        if scope in ("global", "workspace"):
            return self._scanner.scan_scope(scope)
        raise ValueError(f"Unsupported Skill catalog scope: {scope}")

    def search(self, keywords: Sequence[str]) -> list[SkillDefinition]:
        normalized_keywords = self._validate_keywords(keywords)
        ranked: list[tuple[tuple[int, int, int, int, str, str], SkillDefinition]] = []
        for skill in self._scanner.scan():
            name = normalize_search_text(skill.name)
            description = normalize_search_text(skill.description)
            metadata_keywords = tuple(
                normalize_search_text(keyword) for keyword in skill.keywords
            )
            name_hits = 0
            metadata_hits = 0
            description_hits = 0
            matched = 0
            for keyword in normalized_keywords:
                pattern = re.compile(re.escape(keyword))
                name_match = pattern.search(name) is not None
                metadata_match = any(
                    pattern.search(candidate) is not None
                    for candidate in metadata_keywords
                )
                description_match = pattern.search(description) is not None
                name_hits += int(name_match)
                metadata_hits += int(metadata_match)
                description_hits += int(description_match)
                matched += int(name_match or metadata_match or description_match)
            if not matched:
                continue
            sort_key = (
                -matched,
                -name_hits,
                -metadata_hits,
                -description_hits,
                skill.normalized_name,
                str(skill.path),
            )
            ranked.append((sort_key, skill))
        ranked.sort(key=lambda item: item[0])
        return [skill for _, skill in ranked[:_SEARCH_LIMIT]]

    def find_visible(self, name: str) -> SkillDefinition | None:
        normalized_name = self._validate_name(name)
        return next(
            (
                skill
                for skill in self._scanner.scan()
                if skill.normalized_name == normalized_name
            ),
            None,
        )

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string")
        return normalize_skill_name(name)

    @staticmethod
    def _validate_keywords(keywords: Sequence[str]) -> tuple[str, ...]:
        if isinstance(keywords, (str, bytes)) or not 1 <= len(keywords) <= 8:
            raise ValueError("Search keywords must contain 1 to 8 strings")
        normalized: list[str] = []
        for keyword in keywords:
            if not isinstance(keyword, str) or not keyword.strip():
                raise ValueError("Search keywords must be non-empty strings")
            value = normalize_search_text(keyword)
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)
