import asyncio
import sys
from types import ModuleType, SimpleNamespace

from apps.agent.src.agent_orchestration.plugins.mcp.backend import (
    FastMCPClientBackend,
)
from apps.agent.src.agent_orchestration.plugins.mcp.config import (
    parse_mcp_servers,
)


class FakeMessageHandler:
    pass


class FakeClient:
    instances = []

    def __init__(self, config, **kwargs):
        self.config = config
        self.kwargs = kwargs
        self.initialize_result = SimpleNamespace(
            serverInfo=SimpleNamespace(title="Browser", version="1.0"),
            capabilities=SimpleNamespace(tools=object(), resources=None, prompts=None),
        )
        self.server_info = SimpleNamespace(title="Browser 4", version="4.0")
        self.server_capabilities = SimpleNamespace(
            tools=object(), resources=object(), prompts=None
        )
        self.closed = False
        self.calls = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def list_tools(self):
        return [
            SimpleNamespace(
                name="navigate",
                title="Navigate",
                description="Open a URL",
                inputSchema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                },
                annotations=None,
                _meta={"source": "test"},
            )
        ]

    async def call_tool_mcp(self, *, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="opened"),
                SimpleNamespace(
                    type="image", data="aW1hZ2U=", mimeType="image/png", _meta=None
                ),
            ],
            structuredContent={"url": arguments["url"]},
            isError=False,
            _meta={"request_id": "one"},
        )

    async def close(self):
        self.closed = True


def install_fake_fastmcp(monkeypatch):
    package = ModuleType("fastmcp")
    package.__path__ = []
    package.Client = FakeClient
    client = ModuleType("fastmcp.client")
    client.__path__ = []
    messages = ModuleType("fastmcp.client.messages")
    messages.MessageHandler = FakeMessageHandler
    monkeypatch.setitem(sys.modules, "fastmcp", package)
    monkeypatch.setitem(sys.modules, "fastmcp.client", client)
    monkeypatch.setitem(sys.modules, "fastmcp.client.messages", messages)
    FakeClient.instances.clear()


def test_fastmcp_backend使用公共client_api并转换边界(monkeypatch):
    install_fake_fastmcp(monkeypatch)
    monkeypatch.setenv("BROWSER_TOKEN", "secret")
    changed = []
    config = parse_mcp_servers(
        {
            "browser": {
                "url": "https://browser.example/mcp",
                "headers": {"Authorization": "Bearer ${BROWSER_TOKEN}"},
            }
        }
    )[0]
    backend = FastMCPClientBackend(
        config, workspace_path="/workspace", tools_changed=lambda: changed.append(True)
    )

    async def run():
        info = await backend.connect()
        tools = await backend.list_tools()
        result = await backend.call_tool("navigate", {"url": "https://example.com"})
        await FakeClient.instances[0].kwargs["message_handler"].on_tool_list_changed(None)
        await backend.close()
        return info, tools, result

    info, tools, result = asyncio.run(run())
    client = FakeClient.instances[0]
    assert client.config["mcpServers"]["browser"]["headers"] == {
        "Authorization": "Bearer secret"
    }
    assert client.kwargs["timeout"] == 120
    assert info.title == "Browser 4"
    assert info.version == "4.0"
    assert info.supports_tools is True
    assert info.supports_resources is True
    assert tools[0].tool_ref == "browser/navigate"
    assert tools[0].input_schema["properties"]["url"]["type"] == "string"
    assert result.content[1].type == "image"
    assert result.structured_content == {"url": "https://example.com"}
    assert changed == [True]
    assert client.closed is True


def test_fastmcp_backend将server日志脱敏后交给logger(monkeypatch):
    install_fake_fastmcp(monkeypatch)
    logger = __import__("unittest.mock").mock.MagicMock()
    config = parse_mcp_servers({"browser": {"command": "browser-mcp"}})[0]
    backend = FastMCPClientBackend(
        config, workspace_path="/workspace", tools_changed=lambda: None,
        logger=logger,
    )

    async def run():
        await backend.connect()
        handler = FakeClient.instances[0].kwargs["log_handler"]
        await handler(
            SimpleNamespace(
                level="error", logger="remote",
                data={"APIKey": "secret", "message": "failed"},
            )
        )
        await backend.close()

    asyncio.run(run())
    assert logger.error.call_args.args[-1] == {
        "APIKey": "[REDACTED]", "message": "failed"
    }


def test_fastmcp_backend保留非对象structured_content(monkeypatch):
    install_fake_fastmcp(monkeypatch)
    config = parse_mcp_servers({"browser": {"command": "browser-mcp"}})[0]
    backend = FastMCPClientBackend(
        config, workspace_path="/workspace", tools_changed=lambda: None
    )

    async def call_with_list():
        await backend.connect()
        client = FakeClient.instances[0]

        async def call_tool_mcp(**kwargs):
            del kwargs
            return SimpleNamespace(
                content=[], structured_content=[1, 2], is_error=False, meta=None
            )

        client.call_tool_mcp = call_tool_mcp
        result = await backend.call_tool("list", {})
        await backend.close()
        return result

    assert asyncio.run(call_with_list()).structured_content == [1, 2]
