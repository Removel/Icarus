"""Assemble and cache stateless agents from framework dependencies."""

from collections.abc import Callable

from icarus.core.agents import BaseAgent, ReActAgent
from icarus.core.hooks import HookDispatcher, HookRegistry
from icarus.core.hooks.wrappers import ObservableAgent, ObservableLLM, ObservableToolExecutor
from icarus.core.llm import BaseLLM, LLMFactory
from icarus.core.tools import ToolExecutionPolicy, ToolExecutor, ToolRegistry
from icarus.core.tools.result_store import ToolResultStore


class AgentAssembly:
    def __init__(
        self,
        *,
        llm_factory: LLMFactory,
        tool_registry: ToolRegistry,
        hook_registry: HookRegistry,
        policy_for_role: Callable[[str], ToolExecutionPolicy],
        tool_result_store: ToolResultStore | None = None,
    ) -> None:
        self.llm_factory = llm_factory
        self.tool_registry = tool_registry
        self.hook_registry = hook_registry
        self.hook_dispatcher = HookDispatcher(hook_registry)
        self.policy_for_role = policy_for_role
        self.tool_result_store = tool_result_store
        self._agents: dict[str, BaseAgent] = {}
        self._llms: dict[str, BaseLLM] = {}

    def get_agent(self, model_role: str) -> BaseAgent:
        existing = self._agents.get(model_role)
        if existing is not None:
            return existing
        llm = self.llm_factory.create_llm(role=model_role)
        try:
            executor = ToolExecutor(
                self.tool_registry,
                policy=self.policy_for_role(model_role),
                result_store=self.tool_result_store,
            )
            agent = ReActAgent(
                model_role=model_role,
                llm=ObservableLLM(llm, self.hook_dispatcher),
                tool_executor=ObservableToolExecutor(executor, self.hook_dispatcher),
            )
            observable = ObservableAgent(agent, self.hook_dispatcher)
        except BaseException:
            llm.close()
            raise
        self._llms[model_role] = llm
        self._agents[model_role] = observable
        return observable

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
