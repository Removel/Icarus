import asyncio

from apps.agent.src.agent_orchestration.capability import ReActAgent
from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPToolDescriptor,
)
from apps.agent.src.agent_orchestration.plugins.mcp.plugin import MCPPlugin
from apps.agent.src.agent_orchestration.plugins.mcp.tools import create_mcp_tools
from apps.agent.src.agent_orchestration.tools import ToolExecutor, ToolRegistry
from apps.agent.src.model_provider.base_llm import BaseLLM
from apps.agent.src.model_provider.types import (
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
)


class BridgeStub:
    is_running = False

    def start(self): self.is_running = True
    def stop(self): self.is_running = False

    async def arun(self, operation):
        return await operation()


class ManagerStub:
    server_names = ("blender",)

    def __init__(self): self.executions = []

    async def search_tools(self, **kwargs):
        del kwargs
        return (
            (
                MCPToolDescriptor(
                    "blender/create_objects",
                    "blender",
                    "create_objects",
                    "Create objects",
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                ),
            ),
            {},
        )

    async def call_tool(self, tool_ref, arguments):
        self.executions.append((tool_ref, dict(arguments)))
        return MCPCallResult(
            content=(MCPContent("text", "created"),),
            structured_content={"count": arguments["count"]},
        )

    async def close(self): pass


class ScriptedLLM(BaseLLM):
    def __init__(self):
        self.index = 0
        self.calls = []

    async def ainvoke(self, messages, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        sequence = [
            ToolCall("search", "mcp_tool_search", {"query": "create objects"}),
            ToolCall(
                "execute",
                "mcp_tool_execute",
                {"tool_ref": "blender/create_objects", "arguments": {"count": 3}},
            ),
        ]
        if self.index < len(sequence):
            call = sequence[self.index]
            self.index += 1
            return LLMResponse(Message("assistant", [], tool_calls=[call]), finish_reason="tool_call")
        return LLMResponse(Message("assistant", [TextPart("done")]), finish_reason="stop")

    def invoke(self, messages, tools=None): raise NotImplementedError
    def stream(self, messages, tools=None): return iter(())
    async def astream(self, messages, tools=None):
        if False: yield
    def close(self): pass
    async def aclose(self): pass


def test_agent通过搜索schema再用固定execute调用mcp工具():
    manager = ManagerStub()
    plugin = MCPPlugin("mcp", manager=manager, bridge=BridgeStub())
    registry = ToolRegistry()
    registry.register_many(create_mcp_tools(plugin))
    registry.freeze()
    llm = ScriptedLLM()
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    async def run():
        await plugin.start()
        response = await agent.ainvoke("", [], "Create three Blender objects")
        await plugin.stop()
        return response

    response = asyncio.run(run())

    assert response.message.content == [TextPart("done")]
    assert manager.executions == [("blender/create_objects", {"count": 3})]
    assert {tool.name for tool in llm.calls[0][1]} == {
        "mcp_tool_list", "mcp_tool_search", "mcp_tool_execute"
    }
    search_result = llm.calls[1][0][-1]
    assert "blender/create_objects" in search_result.content[0].text
    execute_result = llm.calls[2][0][-1]
    assert '"count": 3' in execute_result.content[0].text
