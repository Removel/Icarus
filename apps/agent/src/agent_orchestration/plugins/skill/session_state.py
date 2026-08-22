"""Per-runtime cumulative Skill injection state."""

from collections.abc import Iterable

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    RankedSkill,
    SessionSkillUpdate,
    SkillDefinition,
)


class SessionSkillState:
    def __init__(self, refresh_after_unchanged_turns: int = 7) -> None:
        if refresh_after_unchanged_turns < 1:
            raise ValueError("Refresh interval must be positive")
        self.refresh_after_unchanged_turns = refresh_after_unchanged_turns
        self._selected: dict[str, SkillDefinition] = {}
        self.unchanged_turns = 0
        self._has_injected = False

    @property
    def selected_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self._selected.values())

    def update(
        self,
        selected: Iterable[SkillDefinition | RankedSkill],
    ) -> SessionSkillUpdate:
        added: list[SkillDefinition] = []
        changed = False
        for item in selected:
            skill = item.skill if isinstance(item, RankedSkill) else item
            existing = self._selected.get(skill.normalized_name)
            if existing is None:
                self._selected[skill.normalized_name] = skill
                added.append(skill)
                changed = True
            elif existing != skill:
                self._selected[skill.normalized_name] = skill
                changed = True

        if changed or not self._has_injected:
            self.unchanged_turns = 0
            self._has_injected = True
            return SessionSkillUpdate(
                mode="full",
                skills=self.selected_skills,
                added=tuple(added),
            )

        self.unchanged_turns += 1
        if self.unchanged_turns >= self.refresh_after_unchanged_turns:
            self.unchanged_turns = 0
            return SessionSkillUpdate(
                mode="full",
                skills=self.selected_skills,
            )
        return SessionSkillUpdate(
            mode="unchanged",
            skills=self.selected_skills,
        )
