"""One Session-scoped Agent execution environment."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
import logging
from pathlib import Path

from apps.agent.src.agent_orchestration.hooks import (
    HookDispatcher,
    HookRegistry,
    hook_context,
)
from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginManager,
    PluginRuntimeHost,
)
from apps.agent.src.agent_orchestration.plugins.agent import AgentPlugin
from apps.agent.src.agent_orchestration.plugins.persistence import (
    ImageAssetError,
    PersistenceRuntime,
    PersistenceSession,
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.user_input import (
    InputAccepted,
    UserInputPlugin,
)
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskOperationResult,
)
from apps.agent.src.agent_orchestration.tools import ToolRegistry
from apps.agent.src.application.runtime_status import SessionRuntimeSnapshot
from apps.agent.src.application.resource_ref import InvalidResourceError
from apps.agent.src.model_config import ConfigModel
from apps.agent.src.model_provider.types import ImagePart, Message
from apps.agent.src.runtime_update import RuntimeUpdate


UpdatePublisher = Callable[[RuntimeUpdate], Awaitable[None]]


class SessionRuntime:
    """Manage one fixed Session through a manifest-driven Runtime Host."""

    def __init__(
        self,
        identity: SessionIdentity,
        *,
        config: ConfigModel,
        publish_update: UpdatePublisher,
        system_prompt: str = (
            "你是 Icarus Agent。准确理解用户目标，必要时使用工具完成任务，"
            "并在完成后给出清晰的结果。"
        ),
        tools: list[str] | None = None,
        initial_messages: list[Message] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.identity = identity
        self.workspace_path = identity.workspace_path
        self.config = config
        self.logger = logger or logging.getLogger("icarus.agent.session")
        if config.icarus_data_dir is None:
            raise RuntimeError("ICARUS_DATA_DIR is required")

        self.hook_registry = HookRegistry()
        self.persistence = PersistenceRuntime(
            data_dir=config.icarus_data_dir,
            workspace_path=identity.workspace_path,
        )
        self.tool_registry = ToolRegistry()
        self.plugin_manager = PluginManager(
            hook_dispatcher=HookDispatcher(self.hook_registry),
        )
        required = set(config.runtime.required_plugin_ids)
        required.discard("output-bridge")
        required.add("runtime-update")
        self.runtime_host = PluginRuntimeHost(
            identity.workspace_path,
            identity.session_id,
            plugin_dirs=tuple(config.runtime.plugin_dirs),
            required_plugin_ids=frozenset(required),
            plugin_configs={
                **config.runtime.plugin_config,
                "persistence": {
                    **config.runtime.plugin_config.get("persistence", {}),
                    "data_dir": config.icarus_data_dir,
                    "runtime": self.persistence,
                    "hook_registry": self.hook_registry,
                    "runtime_logger": self.logger,
                },
                "skill": {
                    **config.runtime.plugin_config.get("skill", {}),
                    "config_model": config,
                    "hook_registry": self.hook_registry,
                },
                "agent": {
                    **config.runtime.plugin_config.get("agent", {}),
                    "config_model": config,
                    "tool_registry": self.tool_registry,
                    "hook_registry": self.hook_registry,
                },
                "blackboard": {
                    **config.runtime.plugin_config.get("blackboard", {}),
                    "model_role": "thinking",
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "initial_messages": list(initial_messages or []),
                    "config_model": config,
                    "hook_registry": self.hook_registry,
                },
                "runtime-update": {
                    **config.runtime.plugin_config.get("runtime-update", {}),
                    "publish_update": publish_update,
                },
            },
            plugin_manager=self.plugin_manager,
            tool_registry=self.tool_registry,
            logger=self.logger,
        )
        self._user_input: UserInputPlugin | None = None
        self._agent_plugin: AgentPlugin | None = None
        self._started = False
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("SessionRuntime cannot be restarted")
        try:
            with self._context_scope():
                await self.runtime_host.start()
            self._user_input = self.runtime_host.get_capability(
                "user-input", "input"
            )
            self._agent_plugin = self.runtime_host.get_capability(
                "agent", "task_control"
            )
            self._started = True
        except asyncio.CancelledError:
            await self._finish_cancelled_start_cleanup()
            raise
        except Exception:
            await self._cleanup_after_start_failure()
            raise

    async def submit(
        self,
        prompt: str,
        input_images: list[ImagePart | str | Path] | None = None,
        *,
        task_id: str | None = None,
    ) -> InputAccepted:
        if not self._started or self._user_input is None:
            raise RuntimeError("SessionRuntime is not running")
        return await self._user_input.submit(
            prompt,
            input_images,
            task_id=task_id,
        )

    async def checkpoint(self) -> tuple[str, ...]:
        if not self._started:
            raise RuntimeError("SessionRuntime is not running")
        with self._context_scope():
            return await self.runtime_host.checkpoint(("blackboard",))

    def import_resources(self, paths: list[tuple[Path, str | None]]) -> list[ImagePart]:
        session = PersistenceSession(self.persistence, self.identity)
        images = []
        for path, media_type in paths:
            try:
                image = session.import_image(path)
            except ImageAssetError as error:
                raise InvalidResourceError(str(error)) from error
            if media_type is not None and image.media_type != media_type:
                raise InvalidResourceError(
                    "Resource media_type does not match file content"
                )
            images.append(image)
        return images

    async def cancel_task(
        self, task_id: str, reason: str | None = None
    ) -> TaskOperationResult:
        if not self._started or self._agent_plugin is None:
            return TaskOperationResult(task_id=task_id, status="not_running")
        with self._task_context_scope(task_id):
            return self._agent_plugin.handle_task_operation(
                "external",
                TaskCancelRequestedEvent(task_id=task_id, reason=reason),
            )

    def snapshot(self) -> SessionRuntimeSnapshot:
        task_ids = (
            self._agent_plugin.task_channels.active_task_ids
            if self._agent_plugin is not None
            else ()
        )
        runtimes = self.plugin_manager.snapshots()
        return SessionRuntimeSnapshot(
            active_task_ids=task_ids,
            queued_task_count=(
                self._user_input.queued_count
                if self._user_input is not None
                else 0
            ),
            pending_event_count=self.plugin_manager.event_bus.pending_count,
            pending_plugin_event_count=sum(
                item.pending_count for item in runtimes
            ),
            background_work_count=sum(
                item.background_work_count for item in runtimes
            ),
            last_event_at=_latest(item.last_event_at for item in runtimes),
            last_background_work_at=_latest(
                item.last_background_work_at for item in runtimes
            ),
        )

    async def stop(
        self,
        reason: str,
        timeout: float | None = 30,
    ) -> None:
        del reason
        if not self._started:
            return
        stop_task = asyncio.create_task(
            self._stop_impl(timeout),
            name=f"session-runtime:{self.identity.session_id}:stop",
        )
        cancelled = False
        while not stop_task.done():
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                cancelled = True
        error = stop_task.exception()
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error

    async def _stop_impl(self, timeout: float | None) -> None:
        with self._context_scope():
            errors = await self.runtime_host.stop(timeout)
        self._clear_runtime_references()
        self._started = False
        self._closed = True
        if errors:
            raise RuntimeError("; ".join(errors))

    async def _cleanup_after_start_failure(self) -> None:
        try:
            with self._context_scope():
                await self.runtime_host.stop(timeout=5)
        except Exception:
            self.logger.exception("Runtime Host cleanup failed")
        if self.persistence.is_running:
            try:
                self.persistence.stop(drain=False, logger=self.logger)
            except Exception:
                self.logger.exception("Persistence fallback cleanup failed")
        self._clear_runtime_references()
        self._started = False
        self._closed = True

    async def _finish_cancelled_start_cleanup(self) -> None:
        cleanup = asyncio.create_task(self._cleanup_after_start_failure())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        await cleanup

    def _clear_runtime_references(self) -> None:
        self._user_input = None
        self._agent_plugin = None

    def _context_scope(self):
        return hook_context(
            {
                "workspace_path": str(self.identity.workspace_path),
                "workspace_key": self.identity.workspace_key,
                "session_id": self.identity.session_id,
            },
            run_id=None,
        )

    def _task_context_scope(self, task_id: str):
        return hook_context(
            {
                "workspace_path": str(self.identity.workspace_path),
                "workspace_key": self.identity.workspace_key,
                "session_id": self.identity.session_id,
                "task_id": task_id,
            },
            run_id=None,
        )


def _latest(values) -> datetime | None:
    present = tuple(value for value in values if value is not None)
    return max(present) if present else None
