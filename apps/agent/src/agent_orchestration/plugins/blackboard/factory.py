from apps.agent.src.agent_orchestration.hooks import HookDispatcher
from apps.agent.src.agent_orchestration.hooks.wrappers import ObservableLLM
from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistration,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.history_compactor import (
    HistoryCompactor,
)
from apps.agent.src.agent_orchestration.plugins.blackboard.plugin import (
    BlackboardPlugin,
)
from apps.agent.src.agent_orchestration.plugins.persistence.runtime import (
    PersistenceRuntime,
    PersistenceSession,
)
from apps.agent.src.agent_orchestration.plugins.persistence.session_identity import (
    SessionIdentity,
)
from apps.agent.src.model_config import ConfigModel
from apps.agent.src.model_provider.llm_factory import LLMFactory


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
    persistence_runtime = required_capabilities[("persistence", "runtime")]
    identity = required_capabilities[("persistence", "session")]
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
            llm_factory.create_llm("thinking"),
            HookDispatcher(hook_registry),
        )
    )
    plugin = BlackboardPlugin(
        plugin_id,
        required_context_sources=set(
            config.get("required_context_sources", [])
        ),
        model_role=model_role,
        system_prompt=str(config.get("system_prompt", "")),
        tools=config.get("tools"),
        initial_messages=list(config.get("initial_messages", [])),
        context_window=getattr(
            config_model.model_settings, model_role
        ).context_window,
        history_compactor=compactor,
    )
    return PluginRegistration(
        plugin=plugin,
        capabilities=(
            ProvidedCapability("conversation", "1.0.0", plugin),
        ),
        state_provider=plugin,
    )
