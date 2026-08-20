"""Hook 注册表。"""

import logging

from apps.agent.src.agent_orchestration.hooks.base_hook import BaseHook


logger = logging.getLogger(__name__)


class HookRegistry:
    """维护事件名称与 Hook Handler 的映射。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[BaseHook]] = {}

    def register(self, hook_name: str, hook: BaseHook) -> bool:
        if not hook_name.strip():
            logger.error("Skip hook with empty name")
            return False
        if not isinstance(hook, BaseHook):
            logger.error(
                "Skip invalid hook: hook_name=%s type=%s",
                hook_name,
                type(hook).__name__,
            )
            return False
        self._hooks.setdefault(hook_name, []).append(hook)
        return True

    def get_hooks(self, hook_name: str) -> list[BaseHook]:
        if hook_name == "*":
            return list(self._hooks.get("*", []))
        return [
            *self._hooks.get(hook_name, []),
            *self._hooks.get("*", []),
        ]

    def unregister(self, hook_name: str, hook: BaseHook) -> bool:
        hooks = self._hooks.get(hook_name)
        if hooks is None or hook not in hooks:
            return False
        hooks.remove(hook)
        if not hooks:
            del self._hooks[hook_name]
        return True
