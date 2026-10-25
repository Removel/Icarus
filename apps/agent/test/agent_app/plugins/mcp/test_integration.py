import asyncio
import base64

import pytest

from agent_app.plugins.mcp.client_manager import MCPClientManager
from agent_app.plugins.mcp.config import parse_mcp_servers
from agent_app.plugins.mcp.result_converter import MCPResultConverter
from agent_app.plugins.persistence import PersistenceRuntime, PersistenceSession, SessionIdentity
from apps.agent.test.agent_app.plugins.mcp.test_client_manager import FakeBackend

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
    ImagePart,
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


def test_agent模型视图保留混合图片且json只出现一次(tmp_path):
    workspace = tmp_path / "workspace"
    persistence = PersistenceSession(
        PersistenceRuntime(tmp_path / "data", workspace),
        SessionIdentity.create(workspace, "session"),
    )

    class MixedManager(ManagerStub):
        async def call_tool(self, tool_ref, arguments, *, timeout_seconds=None):
            return MCPCallResult(
                structured_content={"unique_fact": "created"},
                content=(
                    MCPContent("text", '{"unique_fact":"created"}'),
                    MCPContent("text", "supplement"),
                    MCPContent("image", base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode("ascii"), media_type="image/png"),
                ),
            )

    plugin = MCPPlugin("mcp", manager=MixedManager(), bridge=BridgeStub(), result_converter=MCPResultConverter(persistence))
    registry = ToolRegistry()
    registry.register_many(create_mcp_tools(plugin))
    llm = ScriptedLLM()
    agent = ReActAgent("thinking", llm, ToolExecutor(registry))

    async def run():
        await plugin.start()
        await agent.ainvoke("", [], "create")
        await plugin.stop()

    asyncio.run(run())
    parts = llm.calls[2][0][-1].content
    assert parts[0].text.count("unique_fact") == 1
    assert "supplement" in parts[0].text
    assert len(parts) == 2 and isinstance(parts[1], ImagePart)
    assert parts[1].source.startswith("assets/")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_execute通过真实manager验证且不回显敏感参数(asynchronous, tmp_path, caplog):
    from apps.agent.test.agent_app.plugins.mcp.test_tools import BridgeStub as SyncBridge

    backends = []

    def factory(config, changed):
        backend = FakeBackend(config.name, changed)
        backends.append(backend)
        return backend

    manager = MCPClientManager(parse_mcp_servers({"blender": {"command": "unused"}}), workspace_path=str(tmp_path), backend_factory=factory)
    plugin = MCPPlugin("mcp", manager=manager, bridge=SyncBridge())
    tool = create_mcp_tools(plugin)[2]
    asyncio.run(plugin.start())
    arguments = {"tool_ref": "blender/create", "arguments": {"count": "synthetic-input-secret"}}
    result = asyncio.run(tool.ainvoke(arguments)) if asynchronous else tool.invoke(arguments)
    assert result.success is False
    assert "$.count: expected type integer" in result.error
    diagnostic_is_safe = "synthetic-input-secret" not in result.error + caplog.text
    assert diagnostic_is_safe
    assert backends[0].calls == []
    asyncio.run(plugin.stop())


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
