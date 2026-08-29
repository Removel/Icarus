"""当前 Agent 实例的统一输入入口。"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
)
from apps.agent.src.agent_orchestration.events import Event, TaskErrorEvent
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    ImageAssetError,
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskChannelRegistry,
    TaskChannelStatus,
)
from apps.agent.src.model_provider.types import ImagePart


@dataclass(frozen=True)
class InputAccepted:
    task_id: str
    queue_position: int


@dataclass(frozen=True)
class PendingInput:
    task_id: str
    prompt: str
    input_images: list[ImagePart | str | Path] = field(default_factory=list)


class UserInputPlugin(BasePlugin):
    """FIFO 启动当前 Agent 实例的一轮轮任务。"""

    def __init__(
        self,
        plugin_id: str,
        session: PersistenceSession,
        agent_plugin_id: str = "agent",
        blackboard_plugin_id: str = "blackboard",
        task_channels: TaskChannelRegistry | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.session = session
        self.agent_plugin_id = agent_plugin_id
        self.blackboard_plugin_id = blackboard_plugin_id
        self.task_channels = task_channels or TaskChannelRegistry()
        self._queue: asyncio.Queue[PendingInput] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._active_task_id: str | None = None
        self._active_completed: asyncio.Event | None = None
        self._active_status: str | None = None
        self._outstanding_count = 0
        self._submit_lock = asyncio.Lock()
        self._accepting_submissions = False

    @property
    def pending_count(self) -> int:
        return self._outstanding_count

    @property
    def queued_count(self) -> int:
        return max(
            0,
            self._outstanding_count
            - (1 if self._active_task_id is not None else 0),
        )

    @property
    def active_task_id(self) -> str | None:
        return self._active_task_id

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(
            self._run_queue(),
            name=f"user-input:{self.plugin_id}",
        )
        self._accepting_submissions = True

    async def submit(
        self,
        prompt: str,
        input_images: list[ImagePart | str | Path] | None = None,
        *,
        task_id: str | None = None,
    ) -> InputAccepted:
        if (
            not self._accepting_submissions
            or self._worker is None
            or self._worker.done()
        ):
            raise RuntimeError("UserInputPlugin is not running")
        async with self._submit_lock:
            task_id = task_id or uuid4().hex
            if self.task_channels.get(task_id) is not None:
                raise ValueError(f"Task already exists: {task_id}")
            queue_position = self._outstanding_count
            self._outstanding_count += 1
            pending = PendingInput(
                task_id=task_id,
                prompt=prompt,
                input_images=list(input_images or []),
            )
            self.task_channels.create(task_id)
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
        if source_plugin_id not in {
            self.agent_plugin_id,
            self.blackboard_plugin_id,
        }:
            return
        if event.task_id != self._active_task_id:
            return
        if isinstance(event, AgentCompletedEvent):
            self._active_status = "completed"
        elif isinstance(event, TaskErrorEvent) and event.fatal:
            self._active_status = "failed"
        elif isinstance(event, AgentCancelledEvent):
            self._active_status = "cancelled"
        else:
            return
        if self._active_completed is not None:
            self._active_completed.set()

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        if isinstance(event, TaskErrorEvent):
            return source_plugin_id in {
                self.agent_plugin_id,
                self.blackboard_plugin_id,
            }
        return source_plugin_id == self.agent_plugin_id

    async def drain(self) -> None:
        await self._queue.join()
        if self._active_completed is not None:
            await self._active_completed.wait()

    async def quiesce(self) -> None:
        self._accepting_submissions = False
        if self._active_task_id is not None:
            await self.publish(
                TaskCancelRequestedEvent(
                    task_id=self._active_task_id,
                    reason="runtime_stopping",
                )
            )

    async def stop(self) -> None:
        self._accepting_submissions = False
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
            channel = self.task_channels.get(pending.task_id)
            if channel is None:
                self._queue.task_done()
                raise RuntimeError(
                    f"Task channel is not found: {pending.task_id}"
                )
            try:
                with self.session.task_scope(pending.task_id):
                    if channel.mark_preparing_context():
                        await self.publish(
                            InputStartedEvent(
                                task_id=pending.task_id,
                            )
                        )
                        try:
                            input_images = self._import_images(
                                pending.input_images
                            )
                        except ImageAssetError as error:
                            self._active_status = "failed"
                            channel.mark_failed()
                            await self.publish(
                                TaskErrorEvent(
                                    task_id=pending.task_id,
                                    fatal=True,
                                    code="image_import_failed",
                                    error_type=type(error).__name__,
                                    error_message=str(error),
                                )
                            )
                        else:
                            await self.publish(
                                UserInputEvent(
                                    task_id=pending.task_id,
                                    prompt=pending.prompt,
                                    input_images=input_images,
                                )
                            )
                        if self._active_status == "failed":
                            await self.publish(
                                InputFinishedEvent(
                                    task_id=pending.task_id,
                                    status="failed",
                                    run_id=channel.run_id,
                                )
                            )
                            continue
                        completed = asyncio.create_task(
                            self._active_completed.wait()
                        )
                        cancelled = asyncio.create_task(
                            channel.wait_cancel_requested()
                        )
                        done, waiting = await asyncio.wait(
                            {completed, cancelled},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for waiter in waiting:
                            waiter.cancel()
                        await asyncio.gather(*waiting, return_exceptions=True)
                        del done
                    if channel.status == TaskChannelStatus.CANCELLING:
                        if channel.run_id is None:
                            self._active_status = "cancelled"
                            channel.mark_cancelled()
                        elif self._active_status is None:
                            await self._active_completed.wait()
                    if self._active_status == "completed":
                        channel.mark_completed()
                    elif self._active_status == "failed":
                        channel.mark_failed()
                    elif self._active_status == "cancelled":
                        channel.mark_cancelled()
                    await self.publish(
                        InputFinishedEvent(
                            task_id=pending.task_id,
                            status=self._active_status or "failed",
                            run_id=channel.run_id,
                        )
                    )
            finally:
                self._active_task_id = None
                self._active_completed = None
                self._active_status = None
                self._outstanding_count -= 1
                self.task_channels.finish(pending.task_id)
                self._queue.task_done()

    def _import_images(
        self, images: list[ImagePart | str | Path]
    ) -> list[ImagePart]:
        return [
            image
            if isinstance(image, ImagePart)
            else self.session.import_image(image)
            for image in images
        ]
