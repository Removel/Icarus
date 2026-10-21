"""Static prompt snapshots and Event-based dynamic prompt collection."""

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path

from agent_app.contracts.prompt import (
    DYNAMIC_LAYERS, STATIC_LAYERS, PromptContributionEvent, PromptError,
    PromptRead, PromptReadRequest, PromptReadResponse, PromptSection, PromptUpdateEvent,
)
from agent_app.plugins.prompt.base_prompt import DEFAULT_SYSTEM_PROMPT
from agent_app.plugins.prompt.composer import compose_prompt, prompt_context_block
from agent_app.plugins.prompt.config import PluginConfig
from agent_app.plugins.prompt.sources import load_workspace_section
from agent_app.plugins.prompt.state import PromptTask
from icarus.core.capabilities import CapabilityStatus
from icarus.core.contracts.context import ContextContributionEvent
from icarus.core.contracts.input import InputFinishedEvent, UserInputEvent
from icarus.core.plugin import BasePlugin, handles
from icarus.core.run_control import (TaskChannelRegistry, TaskChannelStatus, TaskContextInputEvent,
                                     TaskContextInputResultEvent, RuntimeContextRecord, format_runtime_context)
from icarus.core.tools.result_budget import conservative_token_count


class PromptPlugin(BasePlugin):
    config_schema = PluginConfig
    provided_capabilities = (PromptRead,)

    def __init__(self, plugin_id: str, *, workspace_path: Path, config: PluginConfig,
                 instructions: str | None, task_channels: TaskChannelRegistry,
                 logger: logging.Logger, base_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        super().__init__(plugin_id)
        self.workspace_path = workspace_path
        self.config = config
        self.instructions = instructions
        self.task_channels = task_channels
        self.logger = logger
        self.base_prompt = base_prompt
        self._static: dict[str, tuple[PromptSection, ...]] = {}
        self._pending_static: dict[str, tuple[PromptSection, ...]] = {}
        self._dynamic_sources: frozenset[str] = frozenset()
        self._update_sources: frozenset[str] = frozenset()
        self._required: frozenset[str] = frozenset()
        self._measure_dynamic: Callable[[str, str], int] = lambda system, content: len(system) + len(content)
        self._system_prompt = ""
        self._prepared = False
        self._accepting = True
        self._tasks: OrderedDict[str, PromptTask] = OrderedDict()
        self._finished: OrderedDict[str, None] = OrderedDict()
        self._updates: dict[str, tuple[str, str, str, int]] = {}
        self.context_result_source_id: str | None = None
        self._seen_updates: OrderedDict[str, None] = OrderedDict()

    def prepare(self, static_sections: Mapping[str, tuple[PromptSection, ...]], *,
                dynamic_sources: frozenset[str], update_sources: frozenset[str],
                required_sources: frozenset[str], measure_dynamic: Callable[[str, str], int]) -> None:
        if self._prepared:
            raise ValueError("Prompt sources are already prepared")
        if not required_sources <= (set(static_sections) | dynamic_sources | update_sources):
            raise PromptError("prompt_source_failed", "A required prompt contributor is unavailable")
        self._required = required_sources
        for source, sections in static_sections.items():
            try:
                self._validate_sections(sections, ("session", "persona"))
                self._static[source] = tuple(sections)
            except PromptError:
                if source in required_sources:
                    raise
                self.logger.warning("Optional static prompt omitted: source=%s", source)
        self._dynamic_sources = dynamic_sources
        self._update_sources = update_sources
        self._measure_dynamic = measure_dynamic
        self._system_prompt = self._build_static(self._static)
        self._prepared = True

    def _validate_sections(self, sections: tuple[PromptSection, ...], layers: tuple[str, ...]) -> None:
        if not isinstance(sections, tuple) or any(not isinstance(s, PromptSection) or s.layer not in layers for s in sections):
            raise PromptError("prompt_invalid", "Invalid prompt contribution layers")
        if len({s.section_id for s in sections}) != len(sections):
            raise PromptError("prompt_invalid", "Duplicate prompt sections")

    def _build_static(self, sources: Mapping[str, tuple[PromptSection, ...]]) -> str:
        sections = dict(sources)
        sections["base"] = (PromptSection("base", "base", self.base_prompt),)
        workspace = load_workspace_section(self.workspace_path, max_tokens=self.config.max_section_tokens, logger=self.logger)
        if workspace is not None:
            sections["workspace"] = (workspace,)
        if self.instructions is not None:
            sections["external"] = (PromptSection("caller", "external", self.instructions),)
        return compose_prompt(sections, layers=STATIC_LAYERS,
                              required_sources=self._required | {"base", "external"},
                              max_tokens=self.config.max_prompt_tokens,
                              max_section_tokens=self.config.max_section_tokens)

    @handles(PromptReadRequest)
    async def on_read(self, source: str, event: PromptReadRequest):
        try:
            if not self._prepared:
                raise PromptError("prompt_source_failed", "Prompt sources have not been initialized")
            if event.reason == "refresh":
                proposed = {**self._static, **self._pending_static}
                rendered = self._build_static(proposed)
                self._static = proposed
                self._pending_static.clear()
                self._system_prompt = rendered
            return (PromptReadResponse(task_id=event.task_id, correlation_id=event.correlation_id,
                                       status=CapabilityStatus.OK, system_prompt=self._system_prompt),)
        except PromptError as error:
            return (PromptReadResponse(task_id=event.task_id, correlation_id=event.correlation_id,
                                       status=CapabilityStatus.FAILED, error_code=error.code,
                                       error_message=str(error)),)

    @handles(UserInputEvent)
    async def on_input(self, source: str, event: UserInputEvent) -> None:
        if not self._accepting or not event.task_id or event.task_id in self._finished:
            return
        task_id = event.task_id
        task = self._task(task_id)
        if task.received_input:
            return
        task.received_input = True
        task.cutoff = event.occurred_at + timedelta(seconds=self.config.contributor_timeout_seconds)
        task.contributions = {owner: item for owner, item in task.contributions.items()
                              if task.received_at[owner] <= task.cutoff and item.occurred_at <= task.cutoff}
        remaining = (task.cutoff - datetime.now(UTC)).total_seconds()
        if remaining <= 0 or self._dynamic_sources <= task.contributions.keys():
            await self._finalize(event.task_id)
        else:
            async def deadline():
                await asyncio.sleep(max(0, remaining))
                await self._finalize(task_id)
            task.deadline = self.start_background_work(deadline, name=f"prompt-deadline:{task_id}")

    def _task(self, task_id: str) -> PromptTask:
        if task_id not in self._tasks:
            if len(self._tasks) >= 128:
                removable = next((key for key, value in self._tasks.items() if not value.received_input), None)
                if removable is None:
                    raise PromptError("prompt_source_failed", "Too many pending prompt tasks")
                self._tasks.pop(removable)
            self._tasks[task_id] = PromptTask()
        return self._tasks[task_id]

    @handles(PromptContributionEvent)
    async def on_contribution(self, source: str, event: PromptContributionEvent) -> None:
        if not self._accepting or source not in self._dynamic_sources or event.task_id in self._finished:
            return
        assert event.task_id is not None
        task = self._task(event.task_id)
        if task.completed or source in task.contributions:
            return
        received_at = datetime.now(UTC)
        if task.cutoff is not None and (received_at > task.cutoff or event.occurred_at > task.cutoff):
            await self._finalize(event.task_id)
            return
        task.received_at[source] = received_at
        try:
            self._validate_sections(event.sections, DYNAMIC_LAYERS)
            task.contributions[source] = event
        except PromptError:
            task.contributions[source] = PromptContributionEvent(task_id=event.task_id, status="failed")
        if task.received_input and self._dynamic_sources <= task.contributions.keys():
            await self._finalize(event.task_id)

    async def _finalize(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.completed or not self._accepting:
            return
        task.completed = True
        if task.deadline is not None and task.deadline is not asyncio.current_task():
            task.deadline.cancel()
        unavailable = {source for source in self._dynamic_sources if source not in task.contributions or task.contributions[source].status == "failed"}
        try:
            if unavailable & self._required:
                raise PromptError("prompt_source_failed", "A required prompt contributor failed or timed out")
            values = {source: event.sections for source, event in task.contributions.items() if event.status == "completed"}
            content = compose_prompt(values, layers=DYNAMIC_LAYERS, required_sources=self._required,
                                     max_tokens=self.config.max_prompt_tokens, max_section_tokens=self.config.max_section_tokens,
                                     cost=lambda text: self._measure_dynamic(self._system_prompt, text))
            task.initial_content = content
            task.injected_content = content
            task.succeeded = True
            await self.publish(ContextContributionEvent(task_id=task_id, status="completed",
                               context_blocks=[prompt_context_block(content)] if content else []))
        except PromptError as error:
            await self.publish(ContextContributionEvent(task_id=task_id, status="failed", error=error.code))

    @handles(PromptUpdateEvent)
    async def on_update(self, source: str, event: PromptUpdateEvent) -> None:
        if not self._accepting or source not in self._update_sources or event.event_id in self._seen_updates:
            return
        self._seen_updates[event.event_id] = None
        while len(self._seen_updates) > 1024:
            self._seen_updates.popitem(last=False)
        try:
            if event.task_id is None:
                self._validate_sections(event.sections, ("persona",))
                self._pending_static[source] = tuple(event.sections)
                return
            task_id = event.task_id
            self._validate_sections(event.sections, DYNAMIC_LAYERS)
            task = self._tasks.get(task_id)
            channel = self.task_channels.get(task_id)
            if (task is None or not task.succeeded or channel is None
                    or channel.status is not TaskChannelStatus.RUNNING or not channel.run_id
                    or event.task_id in self._finished):
                return
            reserved_tokens = sum(value[3] for value in self._updates.values() if value[0] == event.task_id)
            # Charge each update as its own batch, the conservative maximum when
            # Agent drains several updates together or at separate step boundaries.
            def runtime_cost(text: str) -> int:
                record = RuntimeContextRecord(event.event_id, task_id, self.plugin_id, text, event.occurred_at)
                framed = format_runtime_context((record,)) if text else ""
                return conservative_token_count(framed)

            initial_cost = self._measure_dynamic(self._system_prompt, task.initial_content)
            content = compose_prompt({source: event.sections}, layers=DYNAMIC_LAYERS, required_sources=self._required,
                                     max_tokens=self.config.max_prompt_tokens, max_section_tokens=self.config.max_section_tokens,
                                     cost=lambda text: initial_cost + task.live_tokens + reserved_tokens + runtime_cost(text))
            if not content:
                return
            update = TaskContextInputEvent(task_id=event.task_id, content=content)
            self._updates[update.event_id] = (event.task_id, channel.run_id, content, runtime_cost(content))
            await self.publish(update)
        except PromptError as error:
            self.logger.warning("Prompt update rejected: source=%s code=%s", source, error.code)

    @handles(TaskContextInputResultEvent)
    async def on_context_result(self, source: str, event: TaskContextInputResultEvent) -> None:
        if source != self.context_result_source_id:
            return
        pending = self._updates.get(event.request_event_id)
        if pending is not None:
            task_id, run_id, content, tokens = pending
            if event.task_id != task_id or event.run_id != run_id:
                return
            self._updates.pop(event.request_event_id)
            task = self._tasks.get(task_id)
            if event.status == "accepted" and task is not None:
                task.injected_content += content
                task.live_tokens += tokens
            elif event.status != "accepted":
                self.logger.warning("Prompt update was not accepted: status=%s", event.status)

    @handles(InputFinishedEvent)
    async def on_finished(self, source: str, event: InputFinishedEvent) -> None:
        if event.task_id is None:
            return
        task = self._tasks.pop(event.task_id, None)
        if task is not None and task.deadline is not None:
            task.deadline.cancel()
        self._finished[event.task_id] = None
        while len(self._finished) > 128:
            self._finished.popitem(last=False)
        self._updates = {key: value for key, value in self._updates.items() if value[0] != event.task_id}

    async def quiesce(self) -> None:
        self._accepting = False
        for task in self._tasks.values():
            if task.deadline is not None:
                task.deadline.cancel()

    async def stop(self) -> None:
        await self.quiesce()
        self._tasks.clear()
        self._updates.clear()
