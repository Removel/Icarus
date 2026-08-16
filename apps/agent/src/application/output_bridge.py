"""AgentRuntimeService 的输出事件桥接。"""

import asyncio

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin


class OutputBridgePlugin(BasePlugin):
    """将内部 Plugin Event 原样放入应用层输出队列。"""

    def __init__(self, plugin_id: str = "output-bridge") -> None:
        super().__init__(plugin_id)
        self._events: asyncio.Queue[tuple[str, Event]] = asyncio.Queue()

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        await self._events.put((source_plugin_id, event))

    async def next_event(self) -> tuple[str, Event]:
        return await self._events.get()

    def task_done(self) -> None:
        self._events.task_done()

    def discard_pending(self) -> int:
        discarded = 0
        while True:
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                return discarded
            else:
                discarded += 1
                self._events.task_done()
