"""当前 Agent 实例的统一输入入口。"""

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.plugins.persistence import PersistenceSession
from apps.agent.src.model_provider.types import ImagePart


@dataclass(frozen=True)
class InputAccepted:
    task_id: str
    queue_position: int


@dataclass(frozen=True)
class PendingInput:
    task_id: str
    prompt: str
    input_images: list[ImagePart] = field(default_factory=list)


class UserInputPlugin(BasePlugin):
    """FIFO 启动当前 Agent 实例的一轮轮任务。"""

    def __init__(
        self,
        plugin_id: str,
        session: PersistenceSession,
        agent_plugin_id: str = "agent",
    ) -> None:
        super().__init__(plugin_id)
        self.session = session
        self.agent_plugin_id = agent_plugin_id
        self._queue: asyncio.Queue[PendingInput] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_completed: asyncio.Event | None = None
        self._active_status: str | None = None
        self._outstanding_count = 0
        self._submit_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(
            self._run_queue(),
            name=f"user-input:{self.plugin_id}",
        )

    async def submit(
        self,
        prompt: str,
        input_images: list[ImagePart] | None = None,
    ) -> InputAccepted:
        if self._worker is None or self._worker.done():
            raise RuntimeError("UserInputPlugin is not running")
        async with self._submit_lock:
            task_id = uuid4().hex
            queue_position = self._outstanding_count
            self._outstanding_count += 1
            pending = PendingInput(
                task_id=task_id,
                prompt=prompt,
                input_images=list(input_images or []),
            )
            await self._queue.put(pending)
        with self.session.task_scope(task_id):
            await self.publish(
                InputQueuedEvent(
                    task_id=task_id,
                    queue_position=queue_position,
                )
            )
        return InputAccepted(
            task_id=task_id,
            queue_position=queue_position,
        )

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        if source_plugin_id != self.agent_plugin_id:
            return
        if event.task_id != self._active_task_id:
            return
        if isinstance(event, AgentCompletedEvent):
            self._active_status = "completed"
        elif isinstance(event, AgentErrorEvent):
            self._active_status = "failed"
        else:
            return
        if self._active_completed is not None:
            self._active_completed.set()

    async def drain(self) -> None:
        await self._queue.join()
        if self._active_completed is not None:
            await self._active_completed.wait()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def _run_queue(self) -> None:
        while True:
            pending = await self._queue.get()
            self._active_task_id = pending.task_id
            self._active_completed = asyncio.Event()
            self._active_status = None
            try:
                with self.session.task_scope(pending.task_id):
                    await self.publish(
                        InputStartedEvent(
                            task_id=pending.task_id,
                        )
                    )
                    await self.publish(
                        UserInputEvent(
                            task_id=pending.task_id,
                            prompt=pending.prompt,
                            input_images=pending.input_images,
                        )
                    )
                    await self._active_completed.wait()
                    await self.publish(
                        InputFinishedEvent(
                            task_id=pending.task_id,
                            status=self._active_status or "failed",
                        )
                    )
            finally:
                self._active_task_id = None
                self._active_completed = None
                self._active_status = None
                self._outstanding_count -= 1
                self._queue.task_done()
