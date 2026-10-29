import asyncio

from icarus.core.agents import ReActAgent
from agent_app.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPToolDescriptor,
)
from agent_app.plugins.mcp.plugin import MCPPlugin
from agent_app.plugins.mcp.tools import create_mcp_tools
from icarus.core.tools import ToolExecutor, ToolRegistry
from icarus.core.llm import BaseLLM
from icarus.core.models.messages import (
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

    async def call_tool(self, tool_ref, arguments, *, timeout_seconds=None):
        self.executions.append((tool_ref, dict(arguments), timeout_seconds))
        return MCPCallResult(
            content=(MCPContent("text", "created"),),
            structured_content={"count": arguments["count"]},
        )

    async def close(self): pass


def test_MCP_JSON失败短预览仍有实际原因(tmp_path):
    from agent_app.plugins.mcp.result_converter import MCPResultConverter
    from icarus.core.tools.result_store import ToolResultStore
    from icarus.core.tools.result_budget import conservative_token_count, serialize_tool_result

    converted = MCPResultConverter().convert(MCPCallResult(
        content=(MCPContent("text", '{"reason":"missing target","token":"secret"}'),),
        structured_content={"reason": "missing target", "token": "secret", "log": "x" * 3000},
        is_error=True,
    ))
    executor = ToolExecutor(ToolRegistry(), result_store=ToolResultStore(tmp_path, max_file_bytes=10000))
    call = ToolCall("mcp-failure", "mcp_tool_execute", {})
    result = executor.finalize_group([call], {call.id: converted}, task_id="test-task")[call.id]
    assert result.success is False
    assert "missing target" in result.error
    assert "secret" not in serialize_tool_result(result)
    assert conservative_token_count(serialize_tool_result(result)) <= 1000
    assert result.metadata["result_file_complete"] is True


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
    assert manager.executions == [
        ("blender/create_objects", {"count": 3}, 120)
    ]
    assert {tool.name for tool in llm.calls[0][1]} == {
        "mcp_tool_list", "mcp_tool_search", "mcp_tool_execute"
    }
    search_result = llm.calls[1][0][-1]
    assert "blender/create_objects" in search_result.content[0].text
    execute_result = llm.calls[2][0][-1]
    assert '"count": 3' in execute_result.content[0].text
