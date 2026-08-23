import asyncio

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime import PluginRegistration
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    PROCESS_WORKSPACE_MAINTENANCE_COORDINATOR,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintainer import (
    SkillMaintainer,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_parser import (
    SkillMaintenanceParser,
)
from apps.agent.src.agent_orchestration.plugins.skill.maintenance_prompt import (
    SkillMaintenancePromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.plugin import SkillPlugin
from apps.agent.src.agent_orchestration.plugins.skill.ranker import SkillRanker
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner
from apps.agent.src.agent_orchestration.plugins.skill.session_state import (
    SessionSkillState,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    SkillTurnState,
)
from apps.agent.src.agent_orchestration.plugins.skill.usage_store import (
    SkillUsageStore,
)
from apps.agent.src.model_provider.embedding_factory import EmbeddingFactory
from apps.agent.src.model_config import ConfigModel


async def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id
    persistence = required_capabilities[("persistence", "runtime")]
    identity = required_capabilities[("persistence", "session")]
    redactor = required_capabilities[("persistence", "redactor")]
    hook_registry = config.get("hook_registry")
    if hook_registry is None:
        raise ValueError("skill config requires hook_registry")
    config_model = config.get("config_model")
    if not isinstance(config_model, ConfigModel):
        raise ValueError("skill config requires config_model")
    maintenance_factory = None
    embedding = None
    usage_store = None
    usage_store_task = None
    try:
        maintenance_factory = config.get("maintenance_agent_factory")
        if maintenance_factory is None:
            maintenance_factory = AgentFactory(
                config=config_model,
                hook_registry=hook_registry,
                register_builtin_tools=False,
            )
        embedding = config.get("embedding") or EmbeddingFactory(
            config_model, persistence.resolver.fastembed_cache_dir
        ).create_embedding()
        try:
            usage_store_factory = (
                config.get("usage_store_factory") or SkillUsageStore
            )
            usage_store_task = asyncio.create_task(
                asyncio.to_thread(
                    usage_store_factory,
                    persistence.resolver.skill_state_database,
                ),
                name="skill-usage-store:init",
            )
            usage_store = await asyncio.shield(usage_store_task)
        except asyncio.CancelledError:
            if usage_store_task is not None:
                try:
                    usage_store = await asyncio.shield(usage_store_task)
                except Exception:
                    pass
            raise
        except Exception:
            logger.exception(
                "Skill usage store initialization failed; "
                "continuing without it"
            )
        repository = SkillRepository(
            persistence.resolver.global_skills_dir,
            persistence.resolver.workspace_skills_dir(identity),
            logger=logger,
        )
        plugin = SkillPlugin(
            plugin_id,
            workspace_key=identity.workspace_key,
            user_input_plugin_id="user-input",
            scanner=SkillScanner(
                persistence.resolver.global_skills_dir,
                persistence.resolver.workspace_skills_dir(identity),
                logger=logger,
            ),
            usage_store=usage_store,
            embedding=embedding,
            ranker=SkillRanker(
                minimum_content_score=config_model.skill.minimum_content_score
            ),
            session_state=SessionSkillState(),
            maintainer=SkillMaintainer(
                lambda: maintenance_factory.get_agent("thinking"),
                SkillMaintenancePromptBuilder(redactor),
                SkillMaintenanceParser(),
            ),
            repository=repository,
            coordinator=(
                config.get("maintenance_coordinator")
                or PROCESS_WORKSPACE_MAINTENANCE_COORDINATOR
            ),
            turn_state=SkillTurnState(),
            hook_dispatcher=HookDispatcher(hook_registry),
            maintenance_agent_factory=maintenance_factory,
            logger=logger,
        )
        return PluginRegistration(plugin=plugin)
    except BaseException:
        await _close_partial_resources(
            usage_store, embedding, maintenance_factory
        )
        raise


async def _close_partial_resources(
    usage_store, embedding, maintenance_factory
) -> None:
    operations = []
    if usage_store is not None:
        operations.append(asyncio.to_thread(usage_store.close))
    if embedding is not None:
        operations.append(embedding.aclose())
    if maintenance_factory is not None:
        operations.append(maintenance_factory.aclose())
    if operations:
        await asyncio.gather(*operations, return_exceptions=True)
