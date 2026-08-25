"""Invocation-local filesystem scope for Skill generation tools."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class SkillGenerationContext:
    draft_dir: Path
    workspace_dir: Path
    global_skills_dir: Path
    workspace_skills_dir: Path

    def __post_init__(self) -> None:
        for field_name in (
            "draft_dir",
            "workspace_dir",
            "global_skills_dir",
            "workspace_skills_dir",
        ):
            value = Path(getattr(self, field_name)).expanduser().resolve()
            object.__setattr__(self, field_name, value)
        if not self.draft_dir.is_dir():
            raise ValueError("Skill generation Draft must be a directory")
        if not self.workspace_skills_dir.is_relative_to(self.workspace_dir):
            raise ValueError(
                "Workspace Skills directory must belong to the Workspace"
            )
        if self.draft_dir.parent not in {
            self.workspace_skills_dir / ".drafts",
            self.global_skills_dir / ".drafts",
        }:
            raise ValueError(
                "Skill generation Draft must belong to a configured Draft root"
            )

    @property
    def readable_roots(self) -> tuple[Path, ...]:
        return (self.draft_dir, self.workspace_dir, self.global_skills_dir)


_CURRENT_GENERATION_CONTEXT: ContextVar[SkillGenerationContext | None] = (
    ContextVar("skill_generation_context", default=None)
)


def get_generation_context() -> SkillGenerationContext:
    context = _CURRENT_GENERATION_CONTEXT.get()
    if context is None:
        raise RuntimeError("Skill generation context is not active")
    return context


@contextmanager
def generation_context(
    context: SkillGenerationContext,
) -> Iterator[SkillGenerationContext]:
    token = _CURRENT_GENERATION_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_GENERATION_CONTEXT.reset(token)
