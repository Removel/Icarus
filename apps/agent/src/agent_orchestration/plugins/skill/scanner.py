"""Discover Skill definitions from global and Workspace directories."""

import logging
from pathlib import Path
from typing import Any

import yaml

from apps.agent.src.agent_orchestration.plugins.skill.models import (
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
            for skill in self._scan_scope(self.global_skills_dir, "global")
        }
        discovered.update(
            {
                skill.normalized_name: skill
                for skill in self._scan_scope(
                    self.workspace_skills_dir,
                    "workspace",
                )
            }
        )
        return sorted(
            discovered.values(),
            key=lambda skill: (skill.normalized_name, str(skill.path)),
        )

    def _scan_scope(
        self,
        directory: Path,
        scope: SkillScope,
    ) -> list[SkillDefinition]:
        if not directory.is_dir():
            return []
        definitions: dict[str, SkillDefinition] = {}
        for skill_file in sorted(directory.glob("*/SKILL.md"), key=str):
            try:
                definition = self._parse(skill_file, scope)
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
        return list(definitions.values())

    @staticmethod
    def _parse(skill_file: Path, scope: SkillScope) -> SkillDefinition:
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError("missing YAML front matter")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("invalid YAML front matter opening")
        try:
            closing_index = next(
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            )
        except StopIteration as error:
            raise ValueError("missing YAML front matter closing") from error
        metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
        if not isinstance(metadata, dict):
            raise ValueError("YAML front matter must be a mapping")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Skill description must be a non-empty string")
        return SkillDefinition(
            name=name,
            description=description,
            path=skill_file,
            scope=scope,
            metadata=dict(metadata),
        )
