"""Prioritized hooks, ordered by priority then global registration order."""

import logging
from typing import Any, Callable
from icarus.core.recording import RecordSink
from icarus.core.hooks.base_hook import BaseHook

logger = logging.getLogger(__name__)


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[int, int, BaseHook]]] = {}
        self._sequence = 0
        self._points: dict[tuple[str, str], tuple[type, Callable[[Any, Any], None] | None]] = {}
        self.recorder: RecordSink | None = None

    def register(self, hook_name: str, hook: BaseHook, *, priority: int = 0) -> bool:
        if not hook_name.strip() or not isinstance(hook, BaseHook):
            logger.error("Skip invalid hook registration: %s", hook_name)
            return False
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TypeError("Hook priority must be an integer")
        self._hooks.setdefault(hook_name, []).append((priority, self._sequence, hook))
        self._sequence += 1
        return True

    def get_hooks(self, hook_name: str) -> list[BaseHook]:
        entries = list(self._hooks.get(hook_name, ()))
        if hook_name != "*":
            entries.extend(self._hooks.get("*", ()))
        return [item[2] for item in sorted(entries, key=lambda item: item[:2])]

    def unregister(self, hook_name: str, hook: BaseHook) -> bool:
        entries = self._hooks.get(hook_name, [])
        for entry in entries:
            if entry[2] is hook:
                entries.remove(entry)
                if not entries:
                    self._hooks.pop(hook_name, None)
                return True
        return False
