"""Application composition of model configuration and framework agent assembly."""

from pathlib import Path
from typing import Callable

from agent_app.config import ConfigModel
from agent_app.model_provider.factory import LLMFactory as ModelProviderFactory
from icarus.core.assembly import AgentAssembly
from icarus.core.hooks import HookRegistry
from icarus.core.llm import LLMFactory
from icarus.core.models.messages import ImagePart
from icarus.core.tools import ToolExecutionPolicy, ToolRegistry
from icarus.core.tools.result_store import ToolResultStore


class AgentFactory(AgentAssembly):
    """Compatibility composition root; core never reads ConfigModel."""

    def __init__(
        self,
        config: ConfigModel | None = None,
        llm_factory=None,
        tool_registry: ToolRegistry | None = None,
        hook_registry: HookRegistry | None = None,
        register_builtin_tools: bool = False,
        image_resolver: Callable[[ImagePart], Path] | None = None,
        tool_result_store: ToolResultStore | None = None,
    ) -> None:
        if register_builtin_tools:
            raise ValueError("Built-in tools must be registered by their plugin")
        provider = llm_factory or ModelProviderFactory(config=config, image_resolver=image_resolver)

        def policy_for_role(role: str) -> ToolExecutionPolicy:
            settings = config.agent.tool_execution if config else None
            model = config.get_model(role) if config else None
            return ToolExecutionPolicy(settings, context_window=model.context_window if model else None)

        super().__init__(
            llm_factory=LLMFactory(provider.create_llm),
            tool_registry=tool_registry or ToolRegistry(),
            hook_registry=hook_registry or HookRegistry(),
            policy_for_role=policy_for_role,
            tool_result_store=tool_result_store,
        )
