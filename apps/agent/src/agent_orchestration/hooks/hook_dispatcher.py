"""Hook 统一分发入口。"""

import asyncio
import logging
from typing import Any

from apps.agent.src.agent_orchestration.hooks.hook_context import get_hook_context
from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent
from apps.agent.src.agent_orchestration.hooks.hook_registry import HookRegistry


logger = logging.getLogger(__name__)


class HookDispatcher:
    """构造并分发 HookEvent，隔离 Handler 异常。"""

    def __init__(self, registry: HookRegistry) -> None:
        self.registry = registry

    def trigger(
        self,
        hook_name: str,
        phase: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = self._create_event(hook_name, phase, data)
        for hook in self.registry.get_hooks(hook_name):
            try:
                hook.handle(event)
            except Exception:
                logger.exception(
                    "Hook handler failed: hook_name=%s phase=%s run_id=%s",
                    hook_name,
                    phase,
                    event.run_id,
                )

    async def atrigger(
        self,
        hook_name: str,
        phase: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = self._create_event(hook_name, phase, data)
        hooks = self.registry.get_hooks(hook_name)
        results = await asyncio.gather(
            *(hook.ahandle(event) for hook in hooks),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    "Async hook handler failed: hook_name=%s phase=%s run_id=%s",
                    hook_name,
                    phase,
                    event.run_id,
                    exc_info=(
                        type(result),
                        result,
                        result.__traceback__,
                    ),
                )

    @staticmethod
    def _create_event(
        hook_name: str,
        phase: str,
        data: dict[str, Any] | None,
    ) -> HookEvent:
        context = get_hook_context()
        return HookEvent.create(
            name=hook_name,
            phase=phase,
            run_id=context.run_id if context else None,
            context=context.data if context else None,
            data=data,
        )
