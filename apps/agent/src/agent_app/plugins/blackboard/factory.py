from icarus.core.hooks import HookDispatcher
from icarus.core.hooks.wrappers import ObservableLLM
from icarus.runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from agent_app.plugins.blackboard.history_compactor import (
    HistoryCompactor,
)
from agent_app.plugins.blackboard.plugin import (
    BlackboardPlugin,
)
from agent_app.plugins.blackboard.regions import (
    RegionRegistry,
    RegionStore,
)
from agent_app.plugins.blackboard.tools import (
    create_blackboard_tools,
)
from icarus.runtime.assets import (
    PersistenceRuntime,
    PersistenceSession,
)
from icarus.core.identity import (
    SessionIdentity,
)
from agent_app.config import ConfigModel
from agent_app.model_provider.factory import LLMFactory


def create_plugin(
    plugin_id, workspace_path, session_id, config, required_capabilities,
    logger,
):
    del workspace_path, session_id, logger
    config_model = config.get("config_model")
    if not isinstance(config_model, ConfigModel):
        raise ValueError("blackboard config requires config_model")
    hook_registry = config.get("hook_registry")
    if hook_registry is None:
        raise ValueError("blackboard config requires hook_registry")
    persistence_runtime = config["runtime"]
    identity = config["identity"]
    if not isinstance(persistence_runtime, PersistenceRuntime):
        raise ValueError("blackboard requires persistence runtime")
    if not isinstance(identity, SessionIdentity):
        raise ValueError("blackboard requires persistence session")
    session = PersistenceSession(persistence_runtime, identity)
    model_role = config.get("model_role", "thinking")
    llm_factory = LLMFactory(
        config_model, image_resolver=session.resolve_image
    )
    compactor = HistoryCompactor(
        lambda: ObservableLLM(
            llm_factory.create_llm(config_model.model_roles.compaction),
            HookDispatcher(hook_registry),
        )
    )
    region_registry = RegionRegistry()
    region_store = RegionStore(region_registry)
    plugin = BlackboardPlugin(
        plugin_id,
        required_context_sources=set(config.get("required_context_sources", [])) | {"prompt"},
        fatal_context_sources=set(config.get("fatal_context_sources", [])) | {"prompt"},
        prompt_provider_enabled=True,
        model_role=model_role,
        system_prompt=str(config.get("system_prompt", "")),
        tools=config.get("tools"),
        initial_messages=list(config.get("initial_messages", [])),
        context_window=config_model.get_model(model_role).context_window,
        history_compactor=compactor,
        region_registry=region_registry,
        region_store=region_store,
    )
    plugin._tools = tuple(create_blackboard_tools(region_store))
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("conversation.read", "1.0.0", plugin),
        ),
        tools=(),
        state_provider=plugin,
    )
