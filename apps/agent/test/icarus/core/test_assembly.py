"""Core assembly uses injected dependencies, not application configuration."""

import importlib

from icarus.core.hooks import HookRegistry
from icarus.core.tools import ToolExecutionPolicy, ToolRegistry
from apps.agent.test.agent_app.test_assembly import EchoTool, StubLLMFactory


def test_registry_is_only_tool_source():
    module = importlib.import_module("icarus.core.assembly")
    tools = ToolRegistry()
    tools.register(EchoTool())
    names = tools.names()
    llms = StubLLMFactory()
    assembly = module.AgentAssembly(
        llm_factory=llms,
        tool_registry=tools,
        hook_registry=HookRegistry(),
        policy_for_role=lambda role: ToolExecutionPolicy(),
    )
    first = assembly.get_agent("worker")
    assert first is assembly.get_agent("worker")
    assert tools.names() == names
    result = first.invoke(system_prompt="", history_messages=[], input_prompt="echo", tools=["echo"])
    assert result.message.content[0].text == "worker-done"
    assembly.close()
    assert llms.created["worker"].closed


def test_core_llm_factory_delegates_arbitrary_roles():
    from icarus.core.llm import LLMFactory

    llms = StubLLMFactory()
    factory = LLMFactory(llms.create_llm)
    llm = factory.create_llm("custom-role")
    assert llm.role == "custom-role"
    llm.close()
