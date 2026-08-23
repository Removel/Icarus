"""ReActAgent 的 Plugin 系统适配层。"""

import asyncio
from dataclasses import dataclass, replace
from functools import partial
import logging
from uuid import uuid4

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.capability import (
    AgentCancelledEvent,
    AgentCompletedEvent,
    AgentErrorEvent,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks import HookDispatcher, hook_context
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardContextReadyEvent,
)
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskCancelResultEvent,
    TaskChannel,
    TaskChannelRegistry,
    TaskChannelStatus,
    TaskContextInputEvent,
    TaskContextInputResultEvent,
    TaskOperationResult,
)


logger = logging.getLogger(__name__)


@dataclass
class ActiveAgentRun:
    channel: TaskChannel
    execution_task: asyncio.Task[None]
    execution_started: asyncio.Event


class AgentPlugin(BasePlugin):
    """消费 Blackboard Context，并发布 ReActAgent Stream Event。"""

    def __init__(
        self,
        plugin_id: str,
        agent_factory: AgentFactory,
        task_channels: TaskChannelRegistry | None = None,
        hook_dispatcher: HookDispatcher | None = None,
    ) -> None:
        super().__init__(plugin_id)
        self.agent_factory = agent_factory
        self.task_channels = task_channels or TaskChannelRegistry()
        self.hook_dispatcher = hook_dispatcher
        self._active_runs: dict[str, ActiveAgentRun] = {}

    def accepts_event(self, source_plugin_id: str, event: Event) -> bool:
        del source_plugin_id
        return isinstance(
            event,
            (
                BlackboardContextReadyEvent,
                TaskContextInputEvent,
                TaskCancelRequestedEvent,
            ),
        )

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        if isinstance(event, (TaskContextInputEvent, TaskCancelRequestedEvent)):
            result = self.handle_task_operation(source_plugin_id, event)
            await self.publish(self._operation_result_event(event, result))
            return
        if not isinstance(event, BlackboardContextReadyEvent):
            return
        task_id = event.task_id
        if task_id is None:
            return
        channel = self.task_channels.get(task_id)
        if channel is None:
            return
        run_id = uuid4().hex
        if not channel.start_run(run_id):
            return
        execution_started = asyncio.Event()
        task = asyncio.create_task(
            self._run_agent(event, channel, execution_started),
            name=f"agent-plugin:{self.plugin_id}:{event.task_id}",
        )
        self._active_runs[task_id] = ActiveAgentRun(
            channel,
            task,
            execution_started,
        )
        task.add_done_callback(partial(self._task_completed, task_id))

    async def drain(self) -> None:
        if self._active_runs:
            await asyncio.gather(
                *(run.execution_task for run in self._active_runs.values()),
                return_exceptions=True,
            )

    async def quiesce(self) -> None:
        for task_id in self.task_channels.active_task_ids:
            self.handle_task_operation(
                "runtime",
                TaskCancelRequestedEvent(
                    task_id=task_id,
                    reason="runtime_stopping",
                ),
            )

    async def stop(self) -> None:
        tasks = [run.execution_task for run in self._active_runs.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active_runs.clear()
        await self.agent_factory.aclose()

    def handle_task_operation(
        self,
        source_id: str,
        event: TaskContextInputEvent | TaskCancelRequestedEvent,
    ) -> TaskOperationResult:
        operation = (
            "add_context"
            if isinstance(event, TaskContextInputEvent)
            else "cancel"
        )
        channel = self.task_channels.get(event.task_id) if event.task_id else None
        self._trace_operation(
            "before",
            event,
            operation=operation,
            source_id=source_id,
            run_id=channel.run_id if channel is not None else None,
        )
        if isinstance(event, TaskContextInputEvent):
            result = self._add_task_context(
                event.task_id,
                event.content,
                source_id=source_id,
                event_id=event.event_id,
            )
        else:
            result = self._cancel_task(event.task_id, event.reason)
        self._trace_operation(
            "after",
            event,
            operation=operation,
            source_id=source_id,
            run_id=result.run_id,
            status=result.status,
        )
        return result

    @staticmethod
    def _operation_result_event(
        event: TaskContextInputEvent | TaskCancelRequestedEvent,
        result: TaskOperationResult,
    ) -> TaskContextInputResultEvent | TaskCancelResultEvent:
        result_type = (
            TaskContextInputResultEvent
            if isinstance(event, TaskContextInputEvent)
            else TaskCancelResultEvent
        )
        return result_type(
            task_id=event.task_id,
            request_event_id=event.event_id,
            status=result.status,
            run_id=result.run_id,
        )

    def _add_task_context(
        self,
        task_id: str | None,
        content: str,
        *,
        source_id: str,
        event_id: str | None = None,
    ) -> TaskOperationResult:
        if not task_id:
            return TaskOperationResult(task_id=task_id, status="not_found")
        return self.task_channels.add_context(
            task_id,
            content,
            source_id=source_id,
            event_id=event_id,
        )

    def _cancel_task(
        self,
        task_id: str | None,
        reason: str | None = None,
    ) -> TaskOperationResult:
        if not task_id:
            return TaskOperationResult(task_id=task_id, status="not_found")
        result = self.task_channels.request_cancel(task_id, reason)
        if result.status == "accepted":
            active = self._active_runs.get(task_id)
            if active is not None and active.execution_started.is_set():
                active.execution_task.cancel()
        return result

    async def _run_agent(
        self,
        event: BlackboardContextReadyEvent,
        channel: TaskChannel,
        execution_started: asyncio.Event,
    ) -> None:
        try:
            execution_started.set()
            channel.raise_if_cancelled()
            agent = self.agent_factory.get_agent(event.model_role)
            async for stream_event in agent.astream(
                system_prompt=event.system_prompt,
                history_messages=list(event.history_messages),
                input_prompt=event.input_prompt,
                input_images=list(event.input_images),
                tools=None if event.tools is None else list(event.tools),
                run_control=channel,
            ):
                channel.raise_if_cancelled()
                if isinstance(stream_event, AgentCompletedEvent):
                    if not channel.mark_completed():
                        channel.raise_if_cancelled()
                        return
                elif isinstance(stream_event, AgentErrorEvent):
                    if not channel.mark_failed():
                        channel.raise_if_cancelled()
                        return
                await self._publish_run_event(
                    event.task_id,
                    channel,
                    replace(stream_event, task_id=event.task_id),
                )
                if isinstance(
                    stream_event,
                    (AgentCompletedEvent, AgentErrorEvent),
                ):
                    return
            raise RuntimeError("Agent stream ended without a terminal event")
        except asyncio.CancelledError:
            if channel.status == TaskChannelStatus.CANCELLING:
                if channel.mark_cancelled():
                    await self._publish_run_event(
                        event.task_id,
                        channel,
                        AgentCancelledEvent(
                            task_id=event.task_id,
                            step=channel.current_step,
                            reason=channel.cancel_reason,
                            task_messages=channel.history_checkpoint,
                        )
                    )
                return
            raise
        except Exception as error:
            if channel.mark_failed():
                await self._publish_run_event(
                    event.task_id,
                    channel,
                    AgentErrorEvent(
                        task_id=event.task_id,
                        step=channel.current_step,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                )
                return
            if channel.status == TaskChannelStatus.CANCELLING:
                if channel.mark_cancelled():
                    await self._publish_run_event(
                        event.task_id,
                        channel,
                        AgentCancelledEvent(
                            task_id=event.task_id,
                            step=channel.current_step,
                            reason=channel.cancel_reason,
                            task_messages=channel.history_checkpoint,
                        )
                    )
                return
            raise
        finally:
            self._trace_applied_context(event.task_id, channel)

    def _trace_operation(
        self,
        phase: str,
        event: TaskContextInputEvent | TaskCancelRequestedEvent,
        *,
        operation: str,
        source_id: str,
        run_id: str | None,
        status: str | None = None,
    ) -> None:
        if self.hook_dispatcher is None:
            return
        data = {
            "operation": operation,
            "request_event_id": event.event_id,
            "source_id": source_id,
        }
        if status is not None:
            data["status"] = status
        with hook_context({"task_id": event.task_id}, run_id=run_id):
            self.hook_dispatcher.trigger("task.operation", phase, data)

    def _trace_applied_context(
        self,
        task_id: str,
        channel: TaskChannel,
    ) -> None:
        if self.hook_dispatcher is None:
            return
        with hook_context({"task_id": task_id}, run_id=channel.run_id):
            for batch in channel.applied_batches:
                for record in batch.records:
                    self.hook_dispatcher.trigger(
                        "task.context",
                        "applied",
                        {
                            "request_event_id": record.event_id,
                            "source_id": record.source_id,
                            "applied_before_step": batch.applied_before_step,
                        },
                    )

    async def _publish_run_event(
        self,
        task_id: str,
        channel: TaskChannel,
        event: Event,
    ) -> None:
        with hook_context({"task_id": task_id}, run_id=channel.run_id):
            await self.publish(event)

    def _task_completed(
        self,
        task_id: str,
        task: asyncio.Task[None],
    ) -> None:
        active = self._active_runs.get(task_id)
        if active is not None and active.execution_task is task:
            del self._active_runs[task_id]
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "AgentPlugin task failed: plugin_id=%s",
                self.plugin_id,
                exc_info=(type(error), error, error.__traceback__),
            )
