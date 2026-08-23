"""Application entrypoint for one manifest-driven Agent Runtime."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

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
    PersistencePlugin,
    PersistenceRuntime,
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.skill import SkillPlugin
from apps.agent.src.agent_orchestration.plugins.user_input import (
    InputAccepted,
    UserInputPlugin,
)
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskOperationResult,
)
from apps.agent.src.agent_orchestration.tools import ToolRegistry
from apps.agent.src.application.output_bridge import (
    OutputBridgePlugin,
    OutputEventSubscription,
)
from apps.agent.src.model_config import ConfigModel, get_config
from apps.agent.src.model_provider.types import ImagePart, Message

class AgentRuntimeService:
    """Manage one fixed Session through a manifest-driven Runtime Host."""

    def __init__(
        self,
        workspace_path: str | Path,
        *,
        session_id: str | None = None,
        config: ConfigModel | None = None,
        system_prompt: str = (
            "你是 Icarus Agent。准确理解用户目标，必要时使用工具完成任务，"
            "并在完成后给出清晰的结果。"
        ),
        tools: list[str] | None = None,
        initial_messages: list[Message] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.requested_session_id = session_id
        self._session_id = session_id or uuid4().hex
        self.config = config or get_config()
        self.system_prompt = system_prompt
        self.tools = tools
        self.initial_messages = list(initial_messages or [])
        self.logger = logger or logging.getLogger("icarus.agent.runtime")
        if self.config.icarus_data_dir is None:
            raise RuntimeError("ICARUS_DATA_DIR is required")

        self.hook_registry = HookRegistry()
        self.persistence = PersistenceRuntime(
            data_dir=self.config.icarus_data_dir,
            workspace_path=self.workspace_path,
        )
        self.tool_registry = ToolRegistry()
        self.plugin_manager = PluginManager(
            hook_dispatcher=HookDispatcher(self.hook_registry),
        )
        self.output_bridge = OutputBridgePlugin()
        self.runtime_host = PluginRuntimeHost(
            self.workspace_path,
            self._session_id,
            plugin_dirs=tuple(self.config.runtime.plugin_dirs),
            required_plugin_ids=frozenset(
                self.config.runtime.required_plugin_ids
            ),
            plugin_configs={
                **self.config.runtime.plugin_config,
                "persistence": {
                    **self.config.runtime.plugin_config.get(
                        "persistence", {}
                    ),
                    "data_dir": self.config.icarus_data_dir,
                    "runtime": self.persistence,
                    "hook_registry": self.hook_registry,
                },
                "skill": {
                    **self.config.runtime.plugin_config.get("skill", {}),
                    "config_model": self.config,
                    "hook_registry": self.hook_registry,
                },
                "agent": {
                    **self.config.runtime.plugin_config.get("agent", {}),
                    "config_model": self.config,
                    "tool_registry": self.tool_registry,
                    "hook_registry": self.hook_registry,
                },
                "blackboard": {
                    **self.config.runtime.plugin_config.get(
                        "blackboard", {}
                    ),
                    "required_context_sources": ["skill"],
                    "model_role": "thinking",
                    "system_prompt": self.system_prompt,
                    "tools": self.tools,
                    "initial_messages": self.initial_messages,
                },
                "output-bridge": {
                    **self.config.runtime.plugin_config.get(
                        "output-bridge", {}
                    ),
                    "plugin": self.output_bridge,
                },
            },
            plugin_manager=self.plugin_manager,
            tool_registry=self.tool_registry,
            logger=self.logger,
        )
        self._user_input: UserInputPlugin | None = None
        self._agent_plugin: AgentPlugin | None = None
        self._skill_plugin: SkillPlugin | None = None
        self._persistence_plugin: PersistencePlugin | None = None
        self._started = False
        self._closed = False

    @property
    def session_id(self) -> str | None:
        return self._session_id if self._started else None

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def task_channels(self):
        if self._agent_plugin is None:
            return None
        return self._agent_plugin.task_channels

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("AgentRuntimeService cannot be restarted")
        try:
            with self._context_scope():
                await self.runtime_host.start()
            self._user_input = self.runtime_host.get_capability(
                "user-input", "input"
            )
            self._agent_plugin = self.runtime_host.get_capability(
                "agent", "task_control"
            )
            self._skill_plugin = self.runtime_host.get_plugin("skill")
            self._persistence_plugin = self.runtime_host.get_plugin(
                "persistence"
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
        input_images: list[ImagePart] | None = None,
    ) -> InputAccepted:
        if not self._started or self._user_input is None:
            raise RuntimeError("AgentRuntimeService is not running")
        return await self._user_input.submit(
            prompt=prompt, input_images=input_images
        )

    def subscribe_events(self) -> OutputEventSubscription:
        if not self._started:
            raise RuntimeError("AgentRuntimeService is not running")
        return self.output_bridge.subscribe()

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

    async def stop(self, timeout: float | None = 30) -> None:
        if not self._started:
            return
        stop_task = asyncio.create_task(
            self._stop_impl(timeout), name="agent-runtime:stop"
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
        self.output_bridge.close_subscriptions()
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
        self._skill_plugin = None
        self._persistence_plugin = None

    def _context_scope(self):
        identity = SessionIdentity.create(
            self.workspace_path, self._session_id
        )
        return hook_context(
            {
                "workspace_path": str(identity.workspace_path),
                "workspace_key": identity.workspace_key,
                "session_id": identity.session_id,
            },
            run_id=None,
        )

    def _task_context_scope(self, task_id: str):
        identity = SessionIdentity.create(
            self.workspace_path, self._session_id
        )
        return hook_context(
            {
                "workspace_path": str(identity.workspace_path),
                "workspace_key": identity.workspace_key,
                "session_id": identity.session_id,
                "task_id": task_id,
            },
            run_id=None,
        )
