"""Process-local coordination for explicit Skill writes."""

from collections.abc import Callable
from threading import Lock, RLock
from typing import TypeVar

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    normalize_skill_name,
)


_T = TypeVar("_T")


class SkillWriteCoordinator:
    """Serialize commits that can affect the same Workspace Skill name."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, RLock] = {}

    def run(
        self,
        skill_name: str,
        operation: Callable[[], _T],
    ) -> _T:
        key = self._normalize_name(skill_name)
        with self._guard:
            lock = self._locks.setdefault(key, RLock())
        with lock:
            return operation()

    @staticmethod
    def _normalize_name(skill_name: str) -> str:
        if not isinstance(skill_name, str) or not skill_name.strip():
            raise ValueError("skill_name cannot be empty")
        return normalize_skill_name(skill_name)

PROCESS_SKILL_WRITE_COORDINATOR = SkillWriteCoordinator()
