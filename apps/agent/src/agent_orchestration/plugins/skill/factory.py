"""Factory for the manifest-driven Skill management Plugin."""

from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.skill.catalog import SkillCatalog
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    PROCESS_SKILL_WRITE_COORDINATOR,
)
from apps.agent.src.agent_orchestration.plugins.skill.evolver import SkillEvolver
from apps.agent.src.agent_orchestration.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_tools import (
    create_generation_tools,
)
from apps.agent.src.agent_orchestration.plugins.skill.job_manager import (
    SkillJobManager,
)
from apps.agent.src.agent_orchestration.plugins.skill.plugin import SkillPlugin
from apps.agent.src.agent_orchestration.plugins.skill.producer import SkillProducer
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner
from apps.agent.src.agent_orchestration.plugins.skill.tools import create_skill_tools
from apps.agent.src.agent_orchestration.tools import ToolRegistry
from apps.agent.src.model_config import ConfigModel


async def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger
):
    del session_id
    persistence = required_capabilities[("persistence", "runtime")]
    required_capabilities[("persistence", "session")]
    required_capabilities[("persistence", "state_store")]
    redactor = required_capabilities[("persistence", "redactor")]
    conversation = required_capabilities[("blackboard", "conversation")]
    hook_registry = config.get("hook_registry")
    if hook_registry is None:
        raise ValueError("skill config requires hook_registry")
    config_model = config.get("config_model")
    if not isinstance(config_model, ConfigModel):
        raise ValueError("skill config requires config_model")

    generation_factory = config.get("generation_agent_factory")
    if generation_factory is None:
        generation_tools = ToolRegistry()
        generation_tools.register_many(create_generation_tools())
        generation_tools.freeze()
        generation_factory = AgentFactory(
            config=config_model,
            tool_registry=generation_tools,
            hook_registry=hook_registry,
            register_builtin_tools=False,
        )
    try:
        global_dir = persistence.resolver.global_skills_dir
        workspace_dir = workspace_path.expanduser().resolve() / "skills"
        scanner = SkillScanner(global_dir, workspace_dir, logger=logger)
        catalog = SkillCatalog(scanner)
        repository = SkillRepository(global_dir, workspace_dir, logger=logger)
        prompt_builder = SkillGenerationPromptBuilder(redactor)
        producer = SkillProducer(
            lambda: generation_factory.get_agent("thinking"),
            prompt_builder,
        )
        evolver = SkillEvolver(
            lambda: generation_factory.get_agent("thinking"),
            prompt_builder,
        )
        job_manager = SkillJobManager(
            producer=producer,
            evolver=evolver,
            repository=repository,
            coordinator=(
                config.get("skill_write_coordinator")
                or PROCESS_SKILL_WRITE_COORDINATOR
            ),
            workspace_dir=workspace_path,
            close_resource=generation_factory.aclose,
        )
        plugin = SkillPlugin(
            plugin_id,
            catalog=catalog,
            repository=repository,
            job_manager=job_manager,
            conversation=conversation,
            allow_produce=config_model.skill.allow_produce,
            allow_evolve=config_model.skill.allow_evolve,
            hook_dispatcher=HookDispatcher(hook_registry),
        )
        return PluginRegistration(
            plugin=plugin,
            capabilities=(
                ProvidedCapability("skill_management", "1.0.0", plugin),
            ),
            tools=create_skill_tools(plugin),
            state_provider=plugin,
        )
    except BaseException:
        await generation_factory.aclose()
        raise
