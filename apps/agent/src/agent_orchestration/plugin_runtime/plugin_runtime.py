"""单个 Plugin 的异步运行时。"""

import asyncio
from datetime import UTC, datetime
import logging

from apps.agent.src.agent_orchestration.hooks.hook_context import hook_context
from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_registry import (
    PluginRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    PluginRuntimeSnapshot,
    PluginStatus,
    PublishedEvent,
)


logger = logging.getLogger(__name__)


class PluginRuntime:
    """为一个 Plugin 提供统一 inbox 和顺序消费 Worker。"""

    def __init__(
        self,
        plugin: BasePlugin,
        registry: PluginRegistry,
        inbox_maxsize: int = 0,
    ) -> None:
        self.plugin = plugin
        self.registry = registry
        self._inbox: asyncio.Queue[PublishedEvent] = asyncio.Queue(
            maxsize=inbox_maxsize
        )
        self._worker: asyncio.Task[None] | None = None
        self._accepting = False
        self._accepted_count = 0
        self._handled_count = 0
        self._processed_count = 0
        self._failed_count = 0
        self._last_event_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def plugin_id(self) -> str:
        return self.plugin.plugin_id

    @property
    def status(self) -> PluginStatus:
        return self.registry.get_status(self.plugin_id)

    @property
    def pending_count(self) -> int:
        return self._accepted_count - self._handled_count

    async def start(self) -> None:
        if self.status == PluginStatus.RUNNING:
            return
        if self.status not in {PluginStatus.CREATED, PluginStatus.STOPPED}:
            raise RuntimeError(
                f"Plugin cannot start from status: {self.plugin_id} {self.status.value}"
            )

        self.registry.set_status(self.plugin_id, PluginStatus.STARTING)
        try:
            await self.plugin.start()
            self._accepting = True
            self._worker = asyncio.create_task(
                self._consume_loop(),
                name=f"plugin-runtime:{self.plugin_id}",
            )
            self.registry.set_status(self.plugin_id, PluginStatus.RUNNING)
        except Exception:
            self.registry.set_status(self.plugin_id, PluginStatus.FAILED)
            raise

    async def enqueue(self, published_event: PublishedEvent) -> bool:
        if not self._accepting or self.status != PluginStatus.RUNNING:
            raise RuntimeError(f"Plugin is not accepting events: {self.plugin_id}")
        if not self.plugin.accepts_event(
            published_event.source_plugin_id,
            published_event.event,
        ):
            return False
        await self._inbox.put(published_event)
        self._accepted_count += 1
        return True

    async def drain(self) -> None:
        await self._inbox.join()
        await self.plugin.drain()

    async def stop(self, drain: bool = True) -> None:
        if self.status == PluginStatus.STOPPED:
            return
        if self.status == PluginStatus.CREATED:
            self.registry.set_status(self.plugin_id, PluginStatus.STOPPED)
            return
        if self.status not in {
            PluginStatus.RUNNING,
            PluginStatus.FAILED,
            PluginStatus.STOPPING,
        }:
            raise RuntimeError(
                f"Plugin cannot stop from status: {self.plugin_id} {self.status.value}"
            )

        self.registry.set_status(self.plugin_id, PluginStatus.STOPPING)
        self._accepting = False
        if drain:
            await self.drain()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        try:
            await self.plugin.stop()
        finally:
            self.registry.set_status(self.plugin_id, PluginStatus.STOPPED)

    def snapshot(self) -> PluginRuntimeSnapshot:
        return PluginRuntimeSnapshot(
            plugin_id=self.plugin_id,
            status=self.status,
            queue_size=self._inbox.qsize(),
            queue_capacity=self._inbox.maxsize,
            processed_count=self._processed_count,
            failed_count=self._failed_count,
            last_event_at=self._last_event_at,
            last_error=self._last_error,
        )

    async def _consume_loop(self) -> None:
        while True:
            published_event = await self._inbox.get()
            try:
                with hook_context(
                    published_event.hook_context,
                    run_id=published_event.hook_run_id,
                ):
                    await self._consume(published_event)
            except Exception as error:
                self._failed_count += 1
                self._last_error = str(error)
                logger.exception(
                    "Plugin event consumption failed: plugin_id=%s event_id=%s",
                    self.plugin_id,
                    published_event.event.event_id,
                )
            else:
                self._processed_count += 1
                self._last_error = None
            finally:
                self._handled_count += 1
                self._last_event_at = datetime.now(UTC)
                self._inbox.task_done()

    async def _consume(self, published_event: PublishedEvent) -> None:
        await self.plugin.consume(
            published_event.source_plugin_id,
            published_event.event,
        )
