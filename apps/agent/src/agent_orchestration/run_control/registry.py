"""TaskChannel 的生命周期注册表。"""

from collections import OrderedDict

from apps.agent.src.agent_orchestration.run_control.channel import TaskChannel
from apps.agent.src.agent_orchestration.run_control.types import TaskOperationResult


class TaskChannelRegistry:
    def __init__(
        self,
        *,
        finished_task_limit: int = 1024,
        max_steps: int = 256,
    ) -> None:
        if finished_task_limit < 0:
            raise ValueError("finished_task_limit cannot be negative")
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.finished_task_limit = finished_task_limit
        self.max_steps = max_steps
        self._channels: dict[str, TaskChannel] = {}
        self._finished_run_ids: OrderedDict[str, str | None] = OrderedDict()

    def create(self, task_id: str) -> TaskChannel:
        if task_id in self._channels or task_id in self._finished_run_ids:
            raise ValueError(f"Task channel already exists: {task_id}")
        channel = TaskChannel(task_id, max_steps=self.max_steps)
        self._channels[task_id] = channel
        return channel

    def get(self, task_id: str) -> TaskChannel | None:
        return self._channels.get(task_id)

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(self._channels)

    def finish(self, task_id: str) -> TaskChannel | None:
        channel = self._channels.pop(task_id, None)
        if channel is not None:
            self._finished_run_ids[task_id] = channel.run_id
            while len(self._finished_run_ids) > self.finished_task_limit:
                self._finished_run_ids.popitem(last=False)
        return channel

    def add_context(
        self,
        task_id: str,
        content: str,
        *,
        source_id: str,
        event_id: str | None = None,
    ) -> TaskOperationResult:
        channel = self.get(task_id)
        if channel is None:
            if task_id in self._finished_run_ids:
                return TaskOperationResult(
                    task_id=task_id,
                    status="already_finished",
                    run_id=self._finished_run_ids[task_id],
                )
            return TaskOperationResult(task_id=task_id, status="not_found")
        return channel.add_context(
            content,
            source_id=source_id,
            event_id=event_id,
        )

    def request_cancel(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> TaskOperationResult:
        channel = self.get(task_id)
        if channel is None:
            if task_id in self._finished_run_ids:
                return TaskOperationResult(
                    task_id=task_id,
                    status="already_finished",
                    run_id=self._finished_run_ids[task_id],
                )
            return TaskOperationResult(task_id=task_id, status="not_found")
        return channel.request_cancel(reason)
