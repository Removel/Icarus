"""单个 Agent Runtime 的应用服务入口。"""

import asyncio
import logging
from pathlib import Path

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.hooks import HookDispatcher, HookRegistry
from apps.agent.src.agent_orchestration.plugin_runtime import PluginManager
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelRequestedEvent,
    TaskChannelRegistry,
    TaskOperationResult,
)
from apps.agent.src.agent_orchestration.plugins import (
    AgentPlugin,
    BlackboardPlugin,
    InputAccepted,
    SkillPlugin,
    UserInputPlugin,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    PersistenceRuntime,
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.plugins.skill import (
    PROCESS_WORKSPACE_MAINTENANCE_COORDINATOR,
    SessionSkillState,
    SkillMaintainer,
    SkillMaintenanceParser,
    SkillMaintenancePromptBuilder,
    SkillRanker,
    SkillRepository,
    SkillScanner,
    SkillTurnState,
    SkillUsageStore,
    WorkspaceMaintenanceCoordinator,
)
from apps.agent.src.application.output_bridge import (
    OutputBridgePlugin,
    OutputEventSubscription,
)
from apps.agent.src.model_config import ConfigModel, get_config
from apps.agent.src.model_provider.base_embedding import BaseEmbedding
from apps.agent.src.model_provider.embedding_factory import EmbeddingFactory
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
        embedding: BaseEmbedding | None = None,
        maintenance_agent_factory: AgentFactory | None = None,
        maintenance_coordinator: WorkspaceMaintenanceCoordinator | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.requested_session_id = session_id
        self.config = config or get_config()
        self.system_prompt = system_prompt
        self.tools = tools
        self.initial_messages = list(initial_messages or [])
        self.embedding = embedding
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
        self.maintenance_agent_factory = maintenance_agent_factory or AgentFactory(
            config=self.config,
            hook_registry=self.hook_registry,
            register_builtin_tools=False,
        )
        self.maintenance_coordinator = (
            maintenance_coordinator
            or PROCESS_WORKSPACE_MAINTENANCE_COORDINATOR
        )
        self.plugin_manager = PluginManager(
            hook_dispatcher=HookDispatcher(self.hook_registry),
        )
        self.output_bridge = OutputBridgePlugin()
        self.task_channels = TaskChannelRegistry()
        self._session_context = None
        self._session: PersistenceSession | None = None
        self._user_input: UserInputPlugin | None = None
        self._agent_plugin: AgentPlugin | None = None
        self._skill_plugin: SkillPlugin | None = None
        self._pending_embedding: BaseEmbedding | None = None
        self._usage_store_task: asyncio.Task[SkillUsageStore] | None = None
        self._pending_usage_store: SkillUsageStore | None = None
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

            user_input = UserInputPlugin(
                "user-input",
                self._session,
                task_channels=self.task_channels,
            )
            resolver = self.persistence.resolver
            embedding = self.embedding or EmbeddingFactory(
                self.config,
                resolver.fastembed_cache_dir,
            ).create_embedding()
            self._pending_embedding = embedding
            usage_store = await self._create_skill_usage_store()
            self._pending_usage_store = usage_store
            repository = SkillRepository(
                resolver.global_skills_dir,
                resolver.workspace_skills_dir(self._session.identity),
                logger=self.logger,
            )
            skill = SkillPlugin(
                "skill",
                workspace_key=self._session.identity.workspace_key,
                user_input_plugin_id="user-input",
                scanner=SkillScanner(
                    resolver.global_skills_dir,
                    resolver.workspace_skills_dir(self._session.identity),
                    logger=self.logger,
                ),
                usage_store=usage_store,
                embedding=embedding,
                ranker=SkillRanker(
                    minimum_content_score=(
                        self.config.skill.minimum_content_score
                    )
                ),
                session_state=SessionSkillState(),
                maintainer=SkillMaintainer(
                    lambda: self.maintenance_agent_factory.get_agent("thinking"),
                    SkillMaintenancePromptBuilder(self.persistence.redactor),
                    SkillMaintenanceParser(),
                ),
                repository=repository,
                coordinator=self.maintenance_coordinator,
                turn_state=SkillTurnState(),
                hook_dispatcher=HookDispatcher(self.hook_registry),
                logger=self.logger,
            )
            self._skill_plugin = skill
            self._pending_embedding = None
            self._pending_usage_store = None
            blackboard = BlackboardPlugin(
                "blackboard",
                required_context_sources={"skill"},
                model_role="thinking",
                system_prompt=self.system_prompt,
                tools=self.tools,
                initial_messages=self.initial_messages,
            )
            agent = AgentPlugin(
                "agent",
                self.agent_factory,
                task_channels=self.task_channels,
                hook_dispatcher=HookDispatcher(self.hook_registry),
            )
            for plugin in (
                user_input,
                skill,
                blackboard,
                agent,
                self.output_bridge,
            ):
                self.plugin_manager.register(plugin)

            self.plugin_manager.subscribe("skill", "user-input")
            self.plugin_manager.subscribe("skill", "agent")
            self.plugin_manager.subscribe("blackboard", "user-input")
            self.plugin_manager.subscribe("blackboard", "skill")
            self.plugin_manager.subscribe("output-bridge", "user-input")
            self.plugin_manager.subscribe("agent", "blackboard")
            self.plugin_manager.subscribe("user-input", "agent")
            self.plugin_manager.subscribe("blackboard", "agent")
            self.plugin_manager.subscribe("output-bridge", "agent")
            with self._session.context_scope():
                await self.plugin_manager.start()
            self._user_input = user_input
            self._agent_plugin = agent
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
            prompt=prompt,
            input_images=input_images,
        )

    def subscribe_events(self) -> OutputEventSubscription:
        if not self._started:
            raise RuntimeError("AgentRuntimeService is not running")
        return self.output_bridge.subscribe()

    async def cancel_task(
        self,
        task_id: str,
        reason: str | None = None,
    ) -> TaskOperationResult:
        if (
            not self._started
            or self._agent_plugin is None
            or self._session is None
        ):
            return TaskOperationResult(task_id=task_id, status="not_running")
        with self._session.task_scope(task_id):
            return self._agent_plugin.handle_task_operation(
                "external",
                TaskCancelRequestedEvent(task_id=task_id, reason=reason),
            )

    async def stop(self, timeout: float | None = 30) -> None:
        if not self._started:
            return
        stop_task = asyncio.create_task(
            self._stop_impl(timeout),
            name="agent-runtime:stop",
        )
        cancelled = False
        while not stop_task.done():
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                cancelled = True
                continue
        error = stop_task.exception()
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error

    async def _stop_impl(self, timeout: float | None) -> None:
        stop_error: BaseException | None = None
        try:
            with self._session.context_scope():
                await self.plugin_manager.stop(timeout=timeout)
        except BaseException as error:
            stop_error = error
        finally:
            self.output_bridge.close_subscriptions()
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
            if self.maintenance_agent_factory is not self.agent_factory:
                try:
                    await self.maintenance_agent_factory.aclose()
                except BaseException as error:
                    if stop_error is None:
                        stop_error = error
                    else:
                        self.logger.exception(
                            "Maintenance AgentFactory cleanup failed",
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
            self._agent_plugin = None
            self._skill_plugin = None
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
        if self._skill_plugin is not None:
            try:
                await self._skill_plugin.stop()
            except Exception:
                self.logger.exception("SkillPlugin cleanup failed")
        await self._cleanup_pending_skill_resources()
        try:
            await self.agent_factory.aclose()
        except Exception:
            self.logger.exception("AgentFactory cleanup failed")
        if self.maintenance_agent_factory is not self.agent_factory:
            try:
                await self.maintenance_agent_factory.aclose()
            except Exception:
                self.logger.exception("Maintenance AgentFactory cleanup failed")
        self._close_session()
        self.persistence.stop(drain=False, logger=self.logger)
        self._user_input = None
        self._agent_plugin = None
        self._skill_plugin = None
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

    def _close_session(self) -> None:
        if self._session_context is not None:
            self._session_context.__exit__(None, None, None)
            self._session_context = None
        self._session = None

    async def _create_skill_usage_store(self) -> SkillUsageStore | None:
        try:
            task = asyncio.create_task(
                asyncio.to_thread(
                    SkillUsageStore,
                    self.persistence.resolver.skill_state_database,
                ),
                name="skill-usage-store:init",
            )
            self._usage_store_task = task
            store = await asyncio.shield(task)
            self._usage_store_task = None
            return store
        except asyncio.CancelledError:
            raise
        except Exception:
            self._usage_store_task = None
            self.logger.exception(
                "Skill usage store initialization failed; "
                "continuing without persisted usage state"
            )
            return None

    async def _cleanup_pending_skill_resources(self) -> None:
        task = self._usage_store_task
        self._usage_store_task = None
        if task is not None:
            try:
                self._pending_usage_store = await asyncio.shield(task)
            except Exception:
                self.logger.exception("Pending SkillUsageStore initialization failed")
        store = self._pending_usage_store
        self._pending_usage_store = None
        if store is not None:
            try:
                await asyncio.to_thread(store.close)
            except Exception:
                self.logger.exception("Pending SkillUsageStore cleanup failed")
        embedding = self._pending_embedding
        self._pending_embedding = None
        if embedding is not None:
            try:
                await embedding.aclose()
            except Exception:
                self.logger.exception("Pending Embedding cleanup failed")
