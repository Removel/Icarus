"""Hook Handler 抽象。"""

from abc import ABC, abstractmethod
import asyncio

from apps.agent.src.agent_orchestration.hooks.hook_event import HookEvent


class BaseHook(ABC):
    """同步 Hook 为必需入口，异步入口默认在线程中兼容执行。"""

    @abstractmethod
    def handle(self, event: HookEvent) -> None:
        ...

    async def ahandle(self, event: HookEvent) -> None:
        await asyncio.to_thread(self.handle, event)
