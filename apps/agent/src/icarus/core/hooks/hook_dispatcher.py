"""Sequential value transformation with isolated per-hook rollback."""

from copy import deepcopy
import inspect
import logging
from typing import Any

from icarus.core.recording import record as record_current
from icarus.core.hooks.hook_context import get_hook_context
from icarus.core.hooks.hook_event import HookEvent
from icarus.core.hooks.hook_registry import HookRegistry

logger = logging.getLogger(__name__)


class HookDispatcher:
    def __init__(self, registry: HookRegistry) -> None:
        self.registry = registry
        self.recorder = getattr(registry, "recorder", None)

    def record(self, name, phase, data=None):
        recorder = self.recorder or getattr(self.registry, "recorder", None)
        if recorder is not None:
            recorder.record(name, phase, data=data or {})
        else:
            record_current(name, phase, data)

    async def arecord(self, name, phase, data=None):
        self.record(name, phase, data)

    def declare_point(self, name: str, phase: str, value_type: type, *, validator=None) -> None:
        key = (name, phase)
        existing = self.registry._points.get(key)
        if existing is not None and existing[0] is not value_type:
            raise TypeError("Hook point already has a different value type")
        self.registry._points[key] = (value_type, validator)

    def _validate(self, name, phase, previous, result):
        expected, validator = self.registry._points[(name, phase)]
        if result is None or not isinstance(result, expected):
            raise TypeError("Hook returned an invalid value type")
        if validator is not None:
            validator(previous, result)
        return result

    def trigger(self, hook_name: str, phase: str, value, *, data=None):
        if (hook_name, phase) not in self.registry._points:
            raise ValueError(f"Undeclared value hook point: {hook_name}.{phase}")
        event = self._create_event(hook_name, phase, data)
        if hook_name not in {"tool.execute", "plugin.consume"}:
            self.record(hook_name, phase, data)
        current = value
        for hook in self.registry.get_hooks(hook_name):
            try:
                result = hook.handle(event, deepcopy(current))
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("Synchronous hook cannot return an awaitable")
                current = self._validate(hook_name, phase, current, result)
            except Exception:
                logger.exception("Hook handler failed: %s %s", hook_name, phase)
        return current

    async def atrigger(self, hook_name: str, phase: str, value, *, data=None):
        if (hook_name, phase) not in self.registry._points:
            raise ValueError(f"Undeclared value hook point: {hook_name}.{phase}")
        event = self._create_event(hook_name, phase, data)
        if hook_name not in {"tool.execute", "plugin.consume"}:
            self.record(hook_name, phase, data)
        current = value
        for hook in self.registry.get_hooks(hook_name):
            try:
                result = await hook.ahandle(event, deepcopy(current))
                current = self._validate(hook_name, phase, current, result)
            except Exception:
                logger.exception("Async hook handler failed: %s %s", hook_name, phase)
        return current

    @staticmethod
    def _create_event(hook_name: str, phase: str, data: dict[str, Any] | None) -> HookEvent:
        context = get_hook_context()
        return HookEvent.create(
            name=hook_name, phase=phase, run_id=context.run_id if context else None,
            context=context.data if context else None, data=data,
        )
