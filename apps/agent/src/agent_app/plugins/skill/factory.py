
from agent_app.utils.redactor import Redactor
"""Factory for the manifest-driven Skill management Plugin."""

from agent_app.assembly import AgentFactory
from icarus.core.hooks import HookDispatcher
from icarus.runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from agent_app.plugins.skill.catalog import SkillCatalog
from agent_app.plugins.skill.coordinator import (
    PROCESS_SKILL_WRITE_COORDINATOR,
)
from agent_app.plugins.skill.evolver import SkillEvolver
from agent_app.plugins.skill.generation_prompt import (
    SkillGenerationPromptBuilder,
)
from agent_app.plugins.skill.generation_tools import (
    create_generation_tools,
)
from agent_app.plugins.skill.job_manager import (
    SkillJobManager,
)
from agent_app.plugins.skill.plugin import SkillPlugin
from agent_app.plugins.skill.producer import SkillProducer
from agent_app.plugins.skill.repository import (
    SkillRepository,
)
from agent_app.plugins.skill.scanner import SkillScanner
from agent_app.plugins.skill.tools import create_skill_tools
from icarus.core.tools import ToolRegistry
from agent_app.config import ConfigModel


async def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities, logger
):
    del session_id
    persistence = config["runtime"]
    config["identity"]
    redactor = Redactor()
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
            lambda: generation_factory.get_agent(config_model.model_roles.skill_generation),
            prompt_builder,
        )
        evolver = SkillEvolver(
            lambda: generation_factory.get_agent(config_model.model_roles.skill_generation),
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
            allow_produce=config_model.skill.allow_produce,
            allow_evolve=config_model.skill.allow_evolve,
            hook_dispatcher=HookDispatcher(hook_registry),
            default_page_size=config_model.agent.tool_execution.default_page_size,
            max_page_size=config_model.agent.tool_execution.max_page_size,
        )
        plugin._tools = tuple(create_skill_tools(plugin))
        return PluginRegistration(
            plugin=plugin,
            capabilities=(),
            tools=(),
            state_provider=plugin,
        )
    except BaseException:
        await generation_factory.aclose()
        raise
