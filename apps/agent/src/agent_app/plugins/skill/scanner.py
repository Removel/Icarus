"""Discover Skill definitions from global and Workspace directories."""

import logging
from pathlib import Path
from typing import Any

import yaml

from agent_app.plugins.skill.models import (
    SkillDefinition,
    SkillScope,
)


class SkillScanner:
    def __init__(
        self,
        global_skills_dir: str | Path,
        workspace_skills_dir: str | Path,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.global_skills_dir = Path(global_skills_dir).expanduser().resolve()
        self.workspace_skills_dir = Path(workspace_skills_dir).expanduser().resolve()
        self.logger = logger or logging.getLogger(__name__)

    def scan(self) -> list[SkillDefinition]:
        """Return definitions sorted by normalized name, Workspace overriding global."""
        discovered = {
            skill.normalized_name: skill
            for skill in self.scan_scope("global")
        }
        discovered.update(
            {
                skill.normalized_name: skill
                for skill in self.scan_scope("workspace")
            }
        )
        return sorted(
            discovered.values(),
            key=lambda skill: (skill.normalized_name, str(skill.path)),
        )

    def scan_scope(self, scope: SkillScope) -> list[SkillDefinition]:
        """Return definitions physically present in one scope without overrides."""
        if scope == "global":
            directory = self.global_skills_dir
        elif scope == "workspace":
            directory = self.workspace_skills_dir
        else:
            raise ValueError(f"Unsupported Skill scope: {scope}")
        if not directory.is_dir():
            return []
        definitions: dict[str, SkillDefinition] = {}
        for skill_file in sorted(directory.glob("*/SKILL.md"), key=str):
            try:
                resolved_file = skill_file.resolve(strict=True)
                if not resolved_file.is_relative_to(directory):
                    self.logger.warning(
                        "Skipping Skill file outside %s root: %s",
                        scope,
                        skill_file,
                    )
                    continue
                definition = self._parse(resolved_file, scope)
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
                self.logger.warning(
                    "Skipping invalid Skill file %s: %s",
                    skill_file,
                    error,
                )
                continue
            if definition.normalized_name in definitions:
                self.logger.warning(
                    "Skipping duplicate %s Skill name %s from %s",
                    scope,
                    definition.name,
                    skill_file,
                )
                continue
            definitions[definition.normalized_name] = definition
        return sorted(
            definitions.values(),
            key=lambda skill: (skill.normalized_name, str(skill.path)),
        )

    def _parse(self, skill_file: Path, scope: SkillScope) -> SkillDefinition:
        remaining = 16 * 1024
        metadata_lines = []
        with skill_file.open("rb") as stream:
            opening = stream.readline(remaining + 1)
            remaining -= len(opening)
            if remaining < 0 or opening.strip() != b"---":
                raise ValueError("invalid YAML front matter opening")
            while remaining > 0:
                line = stream.readline(remaining + 1)
                remaining -= len(line)
                if not line or remaining < 0:
                    raise ValueError("missing or oversized YAML front matter")
                if line.strip() == b"---":
                    break
                metadata_lines.append(line.decode("utf-8"))
            else:
                raise ValueError("oversized YAML front matter")
        metadata = yaml.safe_load("".join(metadata_lines))
        if not isinstance(metadata, dict):
            raise ValueError("YAML front matter must be a mapping")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Skill description must be a non-empty string")
        keywords = self._parse_keywords(metadata.get("keywords"), skill_file)
        return SkillDefinition(
            name=name,
            description=description,
            path=skill_file,
            scope=scope,
            keywords=keywords,
        )

    def _parse_keywords(
        self,
        value: Any,
        skill_file: Path,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= 8
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            self.logger.warning(
                "Ignoring invalid Skill keywords in %s; expected 1 to 8 "
                "non-empty strings",
                skill_file,
            )
            return ()
        return tuple(item.strip() for item in value)
