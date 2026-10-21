"""One Session-scoped Agent execution environment."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from icarus.core.hooks import HookDispatcher, HookRegistry
from icarus.runtime import (
    PluginManager,
    PluginRuntimeHost,
)
from icarus.runtime.session import SessionHost
from icarus.core.run_control import TaskChannelRegistry
from icarus.runtime.assets import PersistenceRuntime
from icarus.core.identity import SessionIdentity
from icarus.core.tools import ToolRegistry
from agent_app.config import ConfigModel
from icarus.core.models.messages import Message
from icarus.core.runtime_update import RuntimeUpdate


UpdatePublisher = Callable[[RuntimeUpdate], Awaitable[None]]


from agent_app.contracts.prompt import PromptContributor, PromptError
from agent_app.plugins.prompt.base_prompt import DEFAULT_SYSTEM_PROMPT
from agent_app.plugins.prompt.composer import prompt_context_block
from icarus.core.tools.result_budget import conservative_token_count


class SessionRuntime(SessionHost):
    """Manage one fixed Session through a manifest-driven Runtime Host."""

    def __init__(
        self,
        identity: SessionIdentity,
        *,
        config: ConfigModel,
        publish_update: UpdatePublisher,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        tools: list[str] | None = None,
        initial_messages: list[Message] | None = None,
        instructions: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.identity = identity
        self.workspace_path = identity.workspace_path
        self.config = config
        self.logger = logger or logging.getLogger("icarus.agent.session")
        if config.icarus_data_dir is None:
            raise RuntimeError("ICARUS_DATA_DIR is required")

        self.hook_registry = HookRegistry()
        self.persistence = PersistenceRuntime(
            data_dir=config.icarus_data_dir,
            workspace_path=identity.workspace_path,
        )
        self.task_channels = TaskChannelRegistry(max_steps=config.agent.max_steps)
        self.tool_registry = ToolRegistry()
        self.plugin_manager = PluginManager(
            hook_dispatcher=HookDispatcher(self.hook_registry),
        )
        required = set(config.runtime.required_plugin_ids)
        self.runtime_host = PluginRuntimeHost(
            identity.workspace_path,
            identity.session_id,
            plugin_dirs=tuple(config.runtime.plugin_dirs),
            builtin_package="agent_app.plugins",
            required_plugin_ids=frozenset(required),
            plugin_configs=self._with_facilities({
                **config.runtime.plugin_config,
                "prompt": {
                    **config.runtime.plugin_config.get("prompt", {}),
                    "instructions": instructions,
                    "base_prompt": system_prompt,
                },
                "persistence": {
                    **config.runtime.plugin_config.get("persistence", {}),
                    "data_dir": config.icarus_data_dir,
                    "runtime": self.persistence,
                    "hook_registry": self.hook_registry,
                    "runtime_logger": self.logger,
                },
                "skill": {
                    **config.runtime.plugin_config.get("skill", {}),
                    "config_model": config,
                    "hook_registry": self.hook_registry,
                },
                "agent": {
                    **config.runtime.plugin_config.get("agent", {}),
                    "config_model": config,
                    "tool_registry": self.tool_registry,
                    "task_channels": self.task_channels,
                    "hook_registry": self.hook_registry,
                },
                "user-input": {
                    **config.runtime.plugin_config.get("user-input", {}),
                    "task_channels": self.task_channels,
                },
                "builtin-tools": {
                    **config.runtime.plugin_config.get("builtin-tools", {}),
                    "max_output_bytes": (
                        config.agent.tool_execution.max_result_file_bytes
                    ),
                    "default_read_lines": (
                        config.agent.tool_execution.default_read_lines
                    ),
                    "max_read_lines": (
                        config.agent.tool_execution.max_read_lines
                    ),
                },
                "blackboard": {
                    **config.runtime.plugin_config.get("blackboard", {}),
                    "model_role": config.model_roles.agent,
                    "tools": tools,
                    "initial_messages": list(initial_messages or []),
                    "config_model": config,
                    "hook_registry": self.hook_registry,
                },
                "runtime-update": {
                    **config.runtime.plugin_config.get("runtime-update", {}),
                    "publish_update": publish_update,
                },
                "mcp": {
                    **config.runtime.plugin_config.get("mcp", {}),
                    "servers": config.mcp_servers,
                    "default_page_size": (
                        config.agent.tool_execution.default_page_size
                    ),
                    "max_page_size": (
                        config.agent.tool_execution.max_page_size
                    ),
                },
                "memory": {
                    **config.runtime.plugin_config.get("memory", {}),
                },
                "persona": {
                    **config.runtime.plugin_config.get("persona", {}),
                    "config_model": config,
                },
                "knowledge": {
                    **config.runtime.plugin_config.get("knowledge", {}),
                    "default_page_size": (
                        config.agent.tool_execution.default_page_size
                    ),
                    "max_page_size": (
                        config.agent.tool_execution.max_page_size
                    ),
                },
                "process": {
                    **config.runtime.plugin_config.get("process", {}),
                    "default_page_size": (
                        config.agent.tool_execution.default_page_size
                    ),
                    "max_page_size": (
                        config.agent.tool_execution.max_page_size
                    ),
                },
            }),
            before_restore=self._register_links,
            before_ready=self._finalize_links,
            plugin_manager=self.plugin_manager,
            tool_registry=self.tool_registry,
            logger=self.logger,
        )
        SessionHost.__init__(self, identity, runtime_host=self.runtime_host,
            assets=self.persistence, task_channels=self.task_channels, logger=self.logger,
            checkpoint_plugin_ids=("blackboard",))

    def _with_facilities(self, configs):
        user_configs = {key: dict(value) for key, value in self.config.runtime.plugin_config.items()}
        for key in configs:
            user_configs.setdefault(key, {})
        user_configs["builtin-tools"] = {k: v for k, v in configs["builtin-tools"].items()}
        user_configs["mcp"] = {k: v for k, v in configs["mcp"].items()}
        user_configs["knowledge"] = {k: v for k, v in configs["knowledge"].items()}
        user_configs["process"] = {k: v for k, v in configs["process"].items()}
        return {key: {**value, "runtime": self.persistence, "identity": self.identity,
                      "task_channels": self.task_channels, "_user_config": user_configs[key]}
                for key, value in configs.items()}

    async def _register_links(self, host):
        from agent_app.plugins.blackboard.plugin import BlackboardPlugin
        from agent_app.plugins.memory.plugin import MemoryPlugin
        boards = [r.plugin for r in host.graph.registrations.values() if isinstance(r.plugin, BlackboardPlugin)]
        memories = [r.plugin for r in host.graph.registrations.values() if isinstance(r.plugin, MemoryPlugin)]
        for board in boards:
            for memory in memories:
                handle = board.region_registry.register(memory.region_definition)
                memory.region_registration = handle

    async def _finalize_links(self, host):
        from agent_app.plugins.blackboard.plugin import BlackboardPlugin
        from agent_app.plugins.prompt.plugin import PromptPlugin
        active = set(host.graph.registrations)
        prompt = next(r.plugin for r in host.graph.registrations.values() if isinstance(r.plugin, PromptPlugin))
        boards = [r.plugin for r in host.graph.registrations.values() if isinstance(r.plugin, BlackboardPlugin)]
        if not boards:
            raise PromptError("prompt_source_failed", "Prompt context consumer is unavailable")
        board = boards[0]
        prompt.context_result_source_id = host.graph.provider_id("task.control")

        def measure(system: str, content: str) -> int:
            serialized = board.prompt_composer.serialize_context([prompt_context_block(content)]) if content else ""
            wrapper = "<plugin_context>\n" + serialized + "\n</plugin_context>" if serialized else ""
            return conservative_token_count(system) + conservative_token_count(wrapper)

        def validate(system: str, content: str) -> None:
            if measure(system, content) > prompt.config.max_prompt_tokens:
                raise PromptError("prompt_too_large", "System and dynamic prompts exceed the total budget")

        static = {}
        dynamic = set()
        updates = set()
        required = frozenset(prompt.config.required_contributors)
        for plugin_id, registration in host.graph.registrations.items():
            plugin = registration.plugin
            if isinstance(plugin, PromptContributor):
                try:
                    static[plugin_id] = plugin.static_prompt_sections()
                except Exception:
                    if plugin_id in required:
                        raise PromptError("prompt_source_failed", "Required static prompt contributor failed") from None
                    self.logger.warning("Optional static prompt omitted: source=%s", plugin_id)
            manifest = host.graph.discovered[plugin_id].manifest
            if "prompt.contribution" in manifest.published_events:
                if "input.user_input" not in manifest.consumed_events:
                    raise PromptError("prompt_invalid", "Dynamic prompt contributor must consume user input")
                dynamic.add(plugin_id)
            if "prompt.update" in manifest.published_events:
                updates.add(plugin_id)
        prompt.prepare(static, dynamic_sources=frozenset(dynamic), update_sources=frozenset(updates),
                       required_sources=required, measure_dynamic=measure)
        for board in boards:
            board.validate_prompt_context = validate
            board.region_registry.remove_inactive_owners(active)
            board.region_store.initialize()
            board.region_registry.freeze()

