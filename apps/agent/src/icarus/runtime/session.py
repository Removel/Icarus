"""Generic session host; application policy is supplied by composition."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from pathlib import Path
from typing import cast
from icarus.core.ports import InputSubmission, TaskControl

from icarus.core.hooks import hook_context
from icarus.core.identity import SessionIdentity
from icarus.core.contracts.input import InputAccepted
from icarus.core.run_control import TaskCancelRequestedEvent, TaskSteerRequestedEvent, TaskOperationResult, TaskChannelRegistry
from icarus.core.models.messages import ImagePart
from icarus.core.resource_ref import InvalidResourceError
from icarus.core.session import SessionRuntimeSnapshot
from icarus.runtime.assets import ImageAssetError, PersistenceRuntime, PersistenceSession
from icarus.runtime.host import PluginRuntimeHost


class SessionHost:
    """Run one fixed plugin graph using injected infrastructure."""

    def __init__(self, identity: SessionIdentity, *, runtime_host: PluginRuntimeHost,
                 assets: PersistenceRuntime, task_channels: TaskChannelRegistry,
                 logger: logging.Logger | None = None,
                 checkpoint_plugin_ids: tuple[str, ...] = ()) -> None:
        self.identity = identity
        self.workspace_path = identity.workspace_path
        self.logger = logger or logging.getLogger("icarus.session")
        self.runtime_host = runtime_host
        self.plugin_manager = runtime_host.plugin_manager
        self.tool_registry = runtime_host.tool_registry
        self.persistence = assets
        self.task_channels = task_channels
        self.checkpoint_plugin_ids = checkpoint_plugin_ids
        self.runtime_host.task_channels = task_channels
        self._user_input: InputSubmission | None = None
        self._agent_plugin: TaskControl | None = None
        self._started = False
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("SessionHost cannot be restarted")
        try:
            self.persistence.start()
            with self._context_scope():
                await self.runtime_host.start()
            self._user_input = cast(InputSubmission, self.runtime_host.get_capability("input.submit"))
            self._agent_plugin = cast(TaskControl, self.runtime_host.get_capability("task.control"))
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
            return await self.runtime_host.checkpoint(self.checkpoint_plugin_ids)

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

    async def steer_task(
        self,
        task_id: str,
        content: str,
        input_images: list[ImagePart] | None = None,
        *,
        display_text: str | None = None,
        submission_id: str | None = None,
    ) -> TaskOperationResult:
        if not self._started or self._agent_plugin is None:
            return TaskOperationResult(task_id=task_id, status="not_running")
        with self._task_context_scope(task_id):
            request = (
                TaskSteerRequestedEvent(
                    task_id=task_id,
                    event_id=submission_id,
                    content=content,
                    input_images=tuple(input_images or ()),
                    display_text=display_text,
                )
                if submission_id is not None
                else TaskSteerRequestedEvent(
                    task_id=task_id,
                    content=content,
                    input_images=tuple(input_images or ()),
                    display_text=display_text,
                )
            )
            return self._agent_plugin.handle_task_operation(
                "user",
                request,
            )

    def snapshot(self) -> SessionRuntimeSnapshot:
        task_ids = (
            self.task_channels.active_task_ids
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
        self.persistence.stop()
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
