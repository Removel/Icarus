import asyncio
from dataclasses import dataclass

from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin


@dataclass(frozen=True, kw_only=True)
class SampleEvent(Event):
    value: str


class RecordingPlugin(BasePlugin):
    def __init__(
        self,
        plugin_id: str,
        *,
        delay: float = 0,
        fail_values: set[str] | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.delay = delay
        self.fail_values = fail_values or set()
        self.received: list[tuple[str, Event]] = []
        self.events: list[Event] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def consume(self, source_plugin_id: str, event: Event) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(event, SampleEvent) and event.value in self.fail_values:
            raise RuntimeError(f"failed: {event.value}")
        self.received.append((source_plugin_id, event))
        self.events.append(event)

    async def stop(self) -> None:
        self.stopped = True
