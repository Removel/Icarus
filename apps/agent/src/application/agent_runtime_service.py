"""单个 Agent Runtime 的应用服务入口。"""

import logging
from pathlib import Path

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.hooks import HookDispatcher, HookRegistry
from apps.agent.src.agent_orchestration.plugin_runtime import PluginManager
from apps.agent.src.agent_orchestration.plugins import (
    AgentPlugin,
    BlackboardPlugin,
    InputAccepted,
    UserInputPlugin,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
    PersistenceSession,
)
from apps.agent.src.application.output_bridge import OutputBridgePlugin
from apps.agent.src.model_config import ConfigModel, get_config
from apps.agent.src.model_provider.types import ImagePart, Message


class AgentRuntimeService:
    """组装并管理一个固定 Session 的 Agent 运行实例。"""

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
        self.config = config or get_config()
        self.system_prompt = system_prompt
        self.tools = tools
        self.initial_messages = list(initial_messages or [])
        self.logger = logger or logging.getLogger("icarus.agent.runtime")

        self.hook_registry = HookRegistry()
        if self.config.icarus_data_dir is None:
            raise RuntimeError("ICARUS_DATA_DIR is required")
        self.persistence = PersistenceRuntime(
            data_dir=self.config.icarus_data_dir,
            workspace_path=self.workspace_path,
        )
        self.agent_factory = AgentFactory(
            config=self.config,
            hook_registry=self.hook_registry,
        )
        self.plugin_manager = PluginManager(
            hook_dispatcher=HookDispatcher(self.hook_registry),
        )
        self.output_bridge = OutputBridgePlugin()
        self._session_context = None
        self._session: PersistenceSession | None = None
        self._user_input: UserInputPlugin | None = None
        self._started = False
        self._closed = False

    @property
    def session_id(self) -> str | None:
        return self._session.identity.session_id if self._session else None

    @property
    def is_running(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("AgentRuntimeService cannot be restarted")
        try:
            self.persistence.start(self.hook_registry, self.logger)
            self._session_context = self.persistence.open_session(
                session_id=self.requested_session_id
            )
            self._session = self._session_context.__enter__()

            user_input = UserInputPlugin("user-input", self._session)
            blackboard = BlackboardPlugin(
                "blackboard",
                required_context_sources=set(),
                model_role="thinking",
                system_prompt=self.system_prompt,
                tools=self.tools,
                initial_messages=self.initial_messages,
            )
            agent = AgentPlugin("agent", self.agent_factory)
            for plugin in (
                user_input,
                blackboard,
                agent,
                self.output_bridge,
            ):
                self.plugin_manager.register(plugin)

            self.plugin_manager.subscribe("blackboard", "user-input")
            self.plugin_manager.subscribe("output-bridge", "user-input")
            self.plugin_manager.subscribe("agent", "blackboard")
            self.plugin_manager.subscribe("user-input", "agent")
            self.plugin_manager.subscribe("blackboard", "agent")
            self.plugin_manager.subscribe("output-bridge", "agent")
            with self._session.context_scope():
                await self.plugin_manager.start()
            self._user_input = user_input
            self._started = True
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
            prompt=prompt,
            input_images=input_images,
        )

    async def next_event(self) -> tuple[str, Event]:
        if not self._started:
            raise RuntimeError("AgentRuntimeService is not running")
        item = await self.output_bridge.next_event()
        self.output_bridge.task_done()
        return item

    async def stop(self, timeout: float | None = 30) -> None:
        if not self._started:
            return
        stop_error: BaseException | None = None
        try:
            with self._session.context_scope():
                await self.plugin_manager.stop(timeout=timeout)
        except BaseException as error:
            stop_error = error
        finally:
            self.output_bridge.discard_pending()
            try:
                await self.agent_factory.aclose()
            except BaseException as error:
                if stop_error is None:
                    stop_error = error
                else:
                    self.logger.exception(
                        "AgentFactory cleanup failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )
            self._close_session()
            try:
                self.persistence.stop(drain=True, logger=self.logger)
            except BaseException as error:
                if stop_error is None:
                    stop_error = error
                else:
                    self.logger.exception(
                        "Persistence cleanup failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )
            self._user_input = None
            self._started = False
            self._closed = True
        if stop_error is not None:
            raise stop_error

    async def _cleanup_after_start_failure(self) -> None:
        if self.plugin_manager.is_running:
            try:
                if self._session is None:
                    await self.plugin_manager.stop(timeout=5)
                else:
                    with self._session.context_scope():
                        await self.plugin_manager.stop(timeout=5)
            except Exception:
                self.logger.exception("PluginManager cleanup failed")
        try:
            await self.agent_factory.aclose()
        except Exception:
            self.logger.exception("AgentFactory cleanup failed")
        self._close_session()
        self.persistence.stop(drain=False, logger=self.logger)
        self._user_input = None
        self._started = False
        self._closed = True

    def _close_session(self) -> None:
        if self._session_context is not None:
            self._session_context.__exit__(None, None, None)
            self._session_context = None
        self._session = None
