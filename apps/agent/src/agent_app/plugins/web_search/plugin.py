"""Per-input search context and truthful progress, delivered through Events."""

import asyncio
from datetime import datetime
from typing import Any

from agent_app.contracts.web_search import WebSearchUpdatedEvent
from agent_app.plugins.web_search.config import PluginConfig
from agent_app.plugins.web_search.pipeline import SearchPipeline
from icarus.core.contracts.context import ContextBlock, ContextContributionEvent
from icarus.core.contracts.input import UserInputEvent, InputFinishedEvent
from icarus.core.events import Event
from icarus.core.plugin import BasePlugin, handles
from icarus.core.run_control import TaskCancelRequestedEvent, TaskChannelStatus


def time_context(options) -> str:
    try:
        now = datetime.fromisoformat(str(options.get("local_time", "")))
        if now.tzinfo is None:
            raise ValueError("Local timestamp requires an offset")
    except ValueError:
        now = datetime.now().astimezone()
    zone = str(options.get("time_zone", ""))
    # Zone names are labels, never executable prompt text.
    if not zone or len(zone) > 100 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/_+-" for c in zone):
        zone = str(now.tzinfo)
    return f"当前时间：{now.isoformat(sep=' ', timespec='seconds')}\n时区：{zone}"


class WebSearchPlugin(BasePlugin):
    config_schema = PluginConfig

    def __init__(self, plugin_id, *, backend, planner, task_channels, deadline_seconds=45):
        super().__init__(plugin_id)
        self.backend = backend
        self.planner = planner
        self.task_channels = task_channels
        self.deadline_seconds = deadline_seconds
        self.work = {}

    @handles(UserInputEvent)
    async def on_input(self, source, event: UserInputEvent):
        if event.task_id in self.work:
            return
        task = self.start_background_work(lambda: self._prepare(event), name=f"web-search:{event.task_id}")
        self.work[event.task_id] = task
        task.add_done_callback(lambda _: self.work.pop(event.task_id, None))

    @handles(TaskCancelRequestedEvent, InputFinishedEvent)
    async def on_stop(self, source, event: Event):
        if isinstance(event, InputFinishedEvent) and event.status != "cancelled":
            return
        task = self.work.get(event.task_id)
        if task:
            task.cancel()

    async def _prepare(self, event):
        clock = time_context(event.request_options)
        snapshot: dict[str, Any] = {"status": "searching"}
        reported = False
        async def report(**values):
            nonlocal reported
            snapshot.update(values)
            await self.publish(WebSearchUpdatedEvent(task_id=event.task_id, **snapshot))
            reported = True
        content = clock
        enabled = event.request_options.get("web_search") is True
        try:
            if enabled:
                if not self.backend.api_key:
                    await report(status="error", notice="联网搜索暂不可用：服务端未配置搜索密钥，已继续正常回答。")
                    content += "\n本轮联网搜索未配置，请正常回答并明确未获得联网资料。"
                else:
                    async def plan(question):
                        return await self.planner(question, clock)
                    pipeline = SearchPipeline(self.backend, plan)
                    context = await asyncio.wait_for(pipeline.run(event.prompt,
                        deep=event.request_options.get("deep_search") is True, report=report), self.deadline_seconds)
                    content += "\n\n" + context
        except asyncio.CancelledError:
            if enabled and reported:
                await report(status="stopped", browsing=False, notice="搜索已停止")
            raise
        except Exception:
            if enabled:
                await report(status="complete" if snapshot.get("sources") else "error", browsing=False,
                    notice="联网搜索暂不可用或超时，已继续正常回答。")
            content += "\n本轮联网搜索未完成，请正常回答，不得声称已浏览网页。"
        channel = self.task_channels.get(event.task_id) if self.task_channels else None
        if channel is not None and channel.status in {TaskChannelStatus.CANCELLING, TaskChannelStatus.CANCELLED}:
            return
        await self.publish(ContextContributionEvent(task_id=event.task_id, status="completed",
            context_blocks=[ContextBlock(source_plugin_id=self.plugin_id, context_type="web-research", content=content)]))

    async def stop(self):
        tasks = tuple(self.work.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.backend.aclose()
