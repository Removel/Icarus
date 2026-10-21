"""Session lifecycle owner for background processes."""

from __future__ import annotations

from agent_app.contracts.prompt import PromptSection
from agent_app.plugins.process.prompts import PROCESS_GUIDANCE
from agent_app.plugins.process.config import PluginConfig

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
import inspect
from pathlib import Path
import os
from threading import RLock, get_ident
from typing import TypeVar

from icarus.core.events import Event
from icarus.core.plugin import BasePlugin
from agent_app.plugins.process.config import ProcessConfig
from agent_app.plugins.process.cursor import ProcessCursorCodec
from icarus.core.contracts.process import (
    ProcessUpdatedEvent,
)
from agent_app.plugins.process.manager import (
    ProcessManager,
    ProcessOperationError,
)
from icarus.core.contracts.process import ProcessSnapshot


T = TypeVar("T")


class ProcessPlugin(BasePlugin):
    config_schema = PluginConfig
    def __init__(
        self,
        plugin_id: str,
        *,
        session_id: str,
        workspace_path: str | Path,
        processes_dir: str | Path,
        config: ProcessConfig,
    ) -> None:
        super().__init__(plugin_id)
        self.config = config
        self._lifecycle_lock = RLock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread_id: int | None = None
        self._started = False
        self.manager = ProcessManager(
            session_id=session_id,
            workspace_path=workspace_path,
            processes_dir=processes_dir,
            config=config,
            cursor_codec=ProcessCursorCodec(os.urandom(32)),
            start_background_work=lambda name, operation: self.start_background_work(
                operation, name=name
            ),
            publish_snapshot=self._publish_snapshot,
        )

    def static_prompt_sections(self):
        return (PromptSection("process-use", "session", PROCESS_GUIDANCE),)

    async def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._owner_loop = asyncio.get_running_loop()
            self._owner_thread_id = get_ident()
            self._started = True
        await self.manager.start_accepting()


    async def quiesce(self) -> None:
        if self._started:
            await self.manager.quiesce()

    async def drain(self) -> None:
        if self._started:
            await self.manager.drain()

    async def stop(self) -> None:
        with self._lifecycle_lock:
            started = self._started
            self._started = False
        try:
            if started:
                await self.manager.shutdown()
        finally:
            with self._lifecycle_lock:
                self._owner_loop = None
                self._owner_thread_id = None

    async def execute(
        self,
        action: str,
        *,
        command: str | None = None,
        workdir: str | None = None,
        process_id: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
        limit_bytes: int | None = None,
        origin_task_id: str | None = None,
    ) -> object:
        self._require_owner_loop()
        if action == "start":
            assert command is not None
            return await self.manager.start(
                command, workdir=workdir, origin_task_id=origin_task_id
            )
        if action == "list":
            return await self.manager.list(cursor=cursor, page_size=page_size)
        if action == "get":
            assert process_id is not None
            return await self.manager.get(process_id)
        if action == "logs":
            assert process_id is not None
            return await self.manager.read_logs(
                process_id, cursor=cursor, limit_bytes=limit_bytes
            )
        if action == "stop":
            assert process_id is not None
            return await self.manager.stop(process_id)
        raise ProcessOperationError("invalid_arguments", "unknown action")

    def run_sync(
        self, operation: Callable[[], Awaitable[T]], *, timeout_seconds: float | None
    ) -> T:
        with self._lifecycle_lock:
            loop = self._owner_loop
            thread_id = self._owner_thread_id
            started = self._started
        if not started or loop is None or loop.is_closed() or not loop.is_running():
            raise ProcessOperationError(
                "tool_unavailable", "process plugin owner loop is not running"
            )
        if thread_id == get_ident():
            raise ProcessOperationError(
                "tool_unavailable",
                "synchronous invocation cannot re-enter the process owner loop",
            )
        async def invoke() -> T:
            return await operation()
        coroutine = invoke()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError as error:
            if inspect.iscoroutine(coroutine):
                coroutine.close()
            raise ProcessOperationError(
                "tool_unavailable", "process plugin owner loop stopped"
            ) from error
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as error:
            future.cancel()
            raise ProcessOperationError(
                "tool_unavailable", "synchronous process operation timed out"
            ) from error

    def _require_owner_loop(self) -> None:
        with self._lifecycle_lock:
            loop = self._owner_loop
            started = self._started
        try:
            current = asyncio.get_running_loop()
        except RuntimeError as error:
            raise ProcessOperationError(
                "tool_unavailable", "process operation requires its owner loop"
            ) from error
        if not started or loop is None or current is not loop:
            raise ProcessOperationError(
                "tool_unavailable", "process operation is outside its owner loop"
            )

    async def _publish_snapshot(self, snapshot: ProcessSnapshot) -> None:
        await self.publish(ProcessUpdatedEvent.from_snapshot(snapshot))

    def build_tools(self):
        return self._tools
