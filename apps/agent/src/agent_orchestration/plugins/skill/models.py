"""Skill discovery, usage, and ranking value objects."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal


SkillScope = Literal["global", "workspace"]
LifecycleStatus = Literal[
    "active",
    "normal",
    "archived",
    "deletion_candidate",
]
InjectionMode = Literal["full", "unchanged"]


def normalize_skill_name(name: str) -> str:
    """Return the stable identity form used for override and persistence."""
    return name.strip().casefold()


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    scope: SkillScope
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = self.name.strip()
        description = self.description.strip()
        if not name:
            raise ValueError("Skill name cannot be empty")
        if not description:
            raise ValueError("Skill description cannot be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "path", self.path.expanduser().resolve())
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def normalized_name(self) -> str:
        return normalize_skill_name(self.name)

    @property
    def skill_key(self) -> str:
        return f"{self.scope}:{self.normalized_name}"


@dataclass(frozen=True)
class SkillUsage:
    workspace_key: str
    skill_key: str
    discovered_at: datetime
    last_used_at: datetime | None = None
    use_count: int = 0


@dataclass(frozen=True)
class RankedSkill:
    skill: SkillDefinition
    content_score: float
    lifecycle_status: LifecycleStatus
    lifecycle_score: float
    final_score: float


@dataclass(frozen=True)
class SessionSkillUpdate:
    mode: InjectionMode
    skills: tuple[SkillDefinition, ...]
    added: tuple[SkillDefinition, ...] = ()
