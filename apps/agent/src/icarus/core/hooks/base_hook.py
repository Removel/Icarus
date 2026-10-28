"""Hook implementations transform a declared value; metadata stays immutable."""

from abc import ABC, abstractmethod
from icarus.core.hooks.hook_event import HookEvent


class BaseHook(ABC):
    @abstractmethod
    def handle(self, event: HookEvent, value):
        ...

    async def ahandle(self, event: HookEvent, value):
        return self.handle(event, value)
