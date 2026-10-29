import asyncio

import pytest

from agent_app.plugins.mcp.plugin import MCPPlugin


class BridgeStub:
    def __init__(self):
        self.is_running = False

    def start(self): self.is_running = True
    def stop(self): self.is_running = False

    async def arun(self, operation):
        return await operation()


class ManagerStub:
    server_names = ("bad",)

    def __init__(self): self.closed = False

    async def close(self): self.closed = True

    async def list_tools(self, **kwargs):
        del kwargs
        return (), 0, {"bad": "ConnectionError: unavailable"}

    async def search_tools(self, **kwargs):
        del kwargs
        return (), {"bad": "ConnectionError: unavailable"}


class EmptyManagerStub:
    server_names = ()

    async def close(self): pass

    async def list_tools(self, **kwargs):
        del kwargs
        return (), 0, {}

    async def search_tools(self, **kwargs):
        del kwargs
        return (), {}


class PartialManagerStub:
    server_names = ("available", "offline")

    async def close(self): pass

    async def list_tools(self, **kwargs):
        del kwargs
        return (), 0, {"offline": "ConnectionError: unavailable"}

    async def search_tools(self, **kwargs):
        del kwargs
        return (), {"offline": "ConnectionError: unavailable"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_plugin最终错误不拼回原始tool_ref(asynchronous):
    seen = []
    reference = "server/tool?token=synthetic-secret&suffix=" + "x" * 400

    class FailingManager(ManagerStub):
        async def call_tool(self, tool_ref, arguments):
            seen.append((tool_ref, arguments))
            raise ValueError("invalid arguments")

    class SyncBridge(BridgeStub):
        def run(self, operation):
            return asyncio.run(operation())

    plugin = MCPPlugin("mcp", manager=FailingManager(), bridge=SyncBridge())
    asyncio.run(plugin.start())
    if asynchronous:
        result = asyncio.run(plugin.aexecute_tool(tool_ref=reference, arguments={}))
    else:
        result = plugin.execute_tool(tool_ref=reference, arguments={})

    assert result.success is False
    assert seen == [(reference, {})]
    assert "synthetic-secret" not in result.error
    assert "x" * 129 not in result.error
    assert len(result.error) < 220
    assert "invalid arguments" in result.error


@pytest.mark.parametrize("asynchronous", [False, True])
def test_plugin真实manager缺失目标诊断有界(asynchronous):
    from agent_app.plugins.mcp.client_manager import MCPClientManager
    from agent_app.plugins.mcp.config import parse_mcp_servers
    from agent_app.plugins.mcp.models import MCPServerInfo

    class Backend:
        async def connect(self):
            return MCPServerInfo("server", supports_tools=True)
        async def list_tools(self):
            return ()
        async def close(self):
            pass
    class Bridge(BridgeStub):
        def run(self, operation):
            return asyncio.run(operation())
    manager = MCPClientManager(parse_mcp_servers({"server": {"url": "https://example.test/mcp"}}),
                               workspace_path="/workspace", backend_factory=lambda *_: Backend())
    plugin = MCPPlugin("mcp", manager=manager, bridge=Bridge())
    asyncio.run(plugin.start())
    reference = "server/" + "r" * 5000
    result = (asyncio.run(plugin.aexecute_tool(tool_ref=reference, arguments={})) if asynchronous
              else plugin.execute_tool(tool_ref=reference, arguments={}))
    assert result.success is False
    assert "not found" in result.error
    assert "r" * 129 not in result.error
    assert len(result.error) < 2400


def test_plugin生命周期不启动bridge且首次工具调用才启动():
    manager = ManagerStub()
    bridge = BridgeStub()
    plugin = MCPPlugin("mcp", manager=manager, bridge=bridge)

    asyncio.run(plugin.start())
    assert bridge.is_running is False
    assert manager.closed is False
    asyncio.run(plugin.alist_tools(server=None, page=1, page_size=20))
    assert bridge.is_running is True
    asyncio.run(plugin.stop())
    assert manager.closed is True
    assert bridge.is_running is False


def test_plugin_quiesce期间保持已接受run的工具可用():
    manager = ManagerStub()
    bridge = BridgeStub()
    plugin = MCPPlugin("mcp", manager=manager, bridge=bridge)

    async def run():
        await plugin.start()
        await plugin.quiesce()
        result = await plugin.alist_tools(server=None, page=1, page_size=20)
        await plugin.stop()
        return result

    assert asyncio.run(run()).success is False
    assert bridge.is_running is False


def test_指定server失败时list和search返回失败():
    manager = ManagerStub()
    bridge = BridgeStub()
    plugin = MCPPlugin("mcp", manager=manager, bridge=bridge)
    asyncio.run(plugin.start())

    listed = asyncio.run(
        plugin.alist_tools(server="bad", page=1, page_size=20)
    )
    searched = asyncio.run(
        plugin.asearch_tools(query="tool", server="bad", limit=5)
    )

    assert listed.success is False
    assert searched.success is False
    assert "unavailable" in listed.error
    assert "Plugin is not running" not in listed.error


def test_无server配置时list和search返回空目录():
    plugin = MCPPlugin(
        "mcp", manager=EmptyManagerStub(), bridge=BridgeStub()
    )

    async def run():
        await plugin.start()
        listed = await plugin.alist_tools(server=None, page=1, page_size=20)
        searched = await plugin.asearch_tools(
            query="anything", server=None, limit=5
        )
        await plugin.stop()
        return listed, searched

    listed, searched = asyncio.run(run())
    assert listed.success is True
    assert listed.output["tools"] == []
    assert searched.success is True
    assert searched.output["matches"] == []


def test_多server部分失败仍标记工具失败供ui展示():
    plugin = MCPPlugin(
        "mcp", manager=PartialManagerStub(), bridge=BridgeStub()
    )

    async def run():
        await plugin.start()
        result = await plugin.asearch_tools(
            query="anything", server=None, limit=5
        )
        await plugin.stop()
        return result

    result = asyncio.run(run())
    assert result.success is False
    assert result.output["server_errors"] == {
        "offline": "ConnectionError: unavailable"
    }
