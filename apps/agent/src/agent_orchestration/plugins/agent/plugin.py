"""ReActAgent 的 Plugin 系统适配层。"""

import asyncio
from dataclasses import replace
import logging

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugin_runtime import BasePlugin
from apps.agent.src.agent_orchestration.plugins.blackboard.events import (
    BlackboardContextReadyEvent,
)


logger = logging.getLogger(__name__)


class AgentPlugin(BasePlugin):
    """消费 Blackboard Context，并发布 ReActAgent Stream Event。"""

    def __init__(
        self,
        plugin_id: str,
        agent_factory: AgentFactory,
    ) -> None:
        super().__init__(plugin_id)
        self.agent_factory = agent_factory
        self._tasks: set[asyncio.Task[None]] = set()

    async def consume(
        self,
        source_plugin_id: str,
        event: Event,
    ) -> None:
        if not isinstance(event, BlackboardContextReadyEvent):
            return
        task = asyncio.create_task(
            self._run_agent(event),
            name=f"agent-plugin:{self.plugin_id}:{event.task_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._task_completed)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run_agent(self, event: BlackboardContextReadyEvent) -> None:
        agent = self.agent_factory.get_agent(event.model_role)
        async for stream_event in agent.astream(
            system_prompt=event.system_prompt,
            history_messages=list(event.history_messages),
            input_prompt=event.input_prompt,
            input_images=list(event.input_images),
            tools=None if event.tools is None else list(event.tools),
        ):
            await self.publish(
                replace(
                    stream_event,
                    task_id=event.task_id,
                )
            )

    def _task_completed(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "AgentPlugin task failed: plugin_id=%s",
                self.plugin_id,
                exc_info=(type(error), error, error.__traceback__),
            )
