"""Agent 编排基础能力的统一组装入口。"""

from apps.agent.src.agent_orchestration.capability.base_agent import BaseAgent
from apps.agent.src.agent_orchestration.capability.react_agent import ReActAgent
from apps.agent.src.agent_orchestration.hooks.hook_dispatcher import (
    HookDispatcher,
)
from apps.agent.src.agent_orchestration.hooks.hook_registry import HookRegistry
from apps.agent.src.agent_orchestration.hooks.wrappers.observable_agent import (
    ObservableAgent,
)
from apps.agent.src.agent_orchestration.hooks.wrappers.observable_llm import (
    ObservableLLM,
)
from apps.agent.src.agent_orchestration.hooks.wrappers.observable_tool_executor import (
    ObservableToolExecutor,
)
from apps.agent.src.agent_orchestration.tools.builtin import create_builtin_tools
from apps.agent.src.agent_orchestration.tools.tool_executor import ToolExecutor
from apps.agent.src.agent_orchestration.tools.tool_registry import ToolRegistry
from apps.agent.src.model_config import ConfigModel, LLMRole
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.llm_factory import LLMFactory


class AgentFactory:
    """按模型角色创建并复用无状态 Agent。"""

    def __init__(
        self,
        config: ConfigModel | None = None,
        llm_factory: LLMFactory | None = None,
        tool_registry: ToolRegistry | None = None,
        hook_registry: HookRegistry | None = None,
        register_builtin_tools: bool = True,
    ) -> None:
        self.llm_factory = llm_factory or LLMFactory(config=config)
        self.tool_registry = tool_registry or ToolRegistry()
        self.hook_registry = hook_registry or HookRegistry()
        self.hook_dispatcher = HookDispatcher(self.hook_registry)
        self._agents: dict[LLMRole, BaseAgent] = {}
        self._llms: dict[LLMRole, BaseLLM] = {}

        if register_builtin_tools:
            self.tool_registry.register_many(create_builtin_tools())

    def get_agent(self, model_role: LLMRole) -> BaseAgent:
        existing = self._agents.get(model_role)
        if existing is not None:
            return existing

        llm = self.llm_factory.create_llm(role=model_role)
        observable_llm = ObservableLLM(llm, self.hook_dispatcher)
        tool_executor = ToolExecutor(self.tool_registry)
        observable_tool_executor = ObservableToolExecutor(
            tool_executor,
            self.hook_dispatcher,
        )
        agent = ReActAgent(
            model_role=model_role,
            llm=observable_llm,
            tool_executor=observable_tool_executor,
        )
        observable_agent = ObservableAgent(agent, self.hook_dispatcher)
        self._llms[model_role] = llm
        self._agents[model_role] = observable_agent
        return observable_agent

    def close(self) -> None:
        first_error: BaseException | None = None
        try:
            for llm in tuple(self._llms.values()):
                try:
                    llm.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            self._agents.clear()
            self._llms.clear()
        if first_error is not None:
            raise first_error

    async def aclose(self) -> None:
        first_error: BaseException | None = None
        try:
            for llm in tuple(self._llms.values()):
                try:
                    await llm.aclose()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            self._agents.clear()
            self._llms.clear()
        if first_error is not None:
            raise first_error
