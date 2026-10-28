"""Value objects shared by Skill discovery and management."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SkillScope = Literal["global", "workspace"]


def normalize_skill_name(name: str) -> str:
    """Return the stable identity form used for override and persistence."""
    return name.strip().casefold()


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    scope: SkillScope
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = self.name.strip()
        description = self.description.strip()
        if not name:
            raise ValueError("Skill name cannot be empty")
        if not description:
            raise ValueError("Skill description cannot be empty")
        if self.scope not in ("global", "workspace"):
            raise ValueError(f"Unsupported Skill scope: {self.scope}")
        keywords = tuple(keyword.strip() for keyword in self.keywords)
        if any(not keyword for keyword in keywords):
            raise ValueError("Skill keywords cannot contain empty strings")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "path", self.path.expanduser().resolve())
        object.__setattr__(self, "keywords", keywords)

    @property
    def normalized_name(self) -> str:
        return normalize_skill_name(self.name)
