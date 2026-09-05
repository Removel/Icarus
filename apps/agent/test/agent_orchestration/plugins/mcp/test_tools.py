import asyncio

from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPToolDescriptor,
)
from apps.agent.src.agent_orchestration.plugins.mcp.plugin import MCPPlugin
from apps.agent.src.agent_orchestration.plugins.mcp.tools import create_mcp_tools


class BridgeStub:
    is_running = True

    def run(self, operation):
        return asyncio.run(operation())

    async def arun(self, operation):
        return await operation()

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False


class ManagerStub:
    server_names = ("blender",)

    def __init__(self):
        self.calls = []

    async def list_tools(self, **kwargs):
        self.calls.append(("list", kwargs))
        return (self._tools(), 1, {})

    async def search_tools(self, **kwargs):
        self.calls.append(("search", kwargs))
        return (self._tools(), {})

    async def call_tool(self, tool_ref, arguments):
        self.calls.append(("execute", tool_ref, arguments))
        return MCPCallResult(
            content=(MCPContent("text", "created"),),
            structured_content={"count": arguments["count"]},
        )

    async def close(self):
        pass

    @staticmethod
    def _tools():
        return (
            MCPToolDescriptor(
                "blender/create", "blender", "create",
                "Create an object",
                {"type": "object", "properties": {"count": {"type": "integer"}}},
            ),
        )


def make_tools():
    manager = ManagerStub()
    plugin = MCPPlugin("mcp", manager=manager, bridge=BridgeStub())
    asyncio.run(plugin.start())
    return manager, {tool.definition.name: tool for tool in create_mcp_tools(plugin)}


def test_mcp_tools固定名称和最小schema():
    _, tools = make_tools()
    assert list(tools) == [
        "mcp_tool_list", "mcp_tool_search", "mcp_tool_execute"
    ]
    assert tools["mcp_tool_list"].definition.input_schema.get("required") is None
    assert tools["mcp_tool_search"].definition.input_schema["required"] == ["query"]
    assert tools["mcp_tool_execute"].definition.input_schema["required"] == ["tool_ref"]


def test_list使用默认分页并返回完整schema():
    manager, tools = make_tools()
    result = tools["mcp_tool_list"].invoke({})

    assert result.success is True
    assert manager.calls == [
        ("list", {"server": None, "page": 1, "page_size": 20})
    ]
    assert result.output["tools"][0]["tool_ref"] == "blender/create"
    assert result.output["tools"][0]["input_schema"]["type"] == "object"


def test_search只要求query并允许多个结果():
    manager, tools = make_tools()
    result = asyncio.run(
        tools["mcp_tool_search"].ainvoke({"query": "create"})
    )

    assert result.success is True
    assert manager.calls == [
        ("search", {"query": "create", "server": None, "limit": 5})
    ]
    assert len(result.output["matches"]) == 1


def test_execute原样传递目标工具参数():
    manager, tools = make_tools()
    arguments = {"object_type": "cube", "location": [1, 2, 0], "count": 3}
    result = asyncio.run(
        tools["mcp_tool_execute"].ainvoke(
            {"tool_ref": "blender/create", "arguments": arguments}
        )
    )

    assert result.success is True
    assert result.output["structured_content"] == {"count": 3}
    assert manager.calls == [("execute", "blender/create", arguments)]


def test_tools拒绝未知字段和错误参数类型():
    _, tools = make_tools()
    assert tools["mcp_tool_list"].invoke({"extra": True}).success is False
    assert tools["mcp_tool_search"].invoke({"query": " "}).success is False
    assert tools["mcp_tool_execute"].invoke(
        {"tool_ref": "blender/create", "arguments": []}
    ).success is False
