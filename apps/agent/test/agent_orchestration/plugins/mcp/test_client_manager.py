import asyncio
from dataclasses import dataclass

import pytest

from apps.agent.src.agent_orchestration.plugins.mcp.catalog import tool_ref
from apps.agent.src.agent_orchestration.plugins.mcp.client_manager import (
    MCPClientManager,
)
from apps.agent.src.agent_orchestration.plugins.mcp.config import (
    parse_mcp_servers,
)
from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPServerInfo,
    MCPToolDescriptor,
)
from apps.agent.src.agent_orchestration.plugins.mcp.client_manager import (
    _validate_arguments,
)


@dataclass
class FakeBackend:
    server: str
    changed: object
    fail_connect: bool = False

    def __post_init__(self):
        self.connect_count = 0
        self.list_count = 0
        self.calls = []
        self.closed = False

    async def connect(self):
        self.connect_count += 1
        await asyncio.sleep(0)
        if self.fail_connect:
            raise ConnectionError(f"{self.server} unavailable")
        return MCPServerInfo(self.server, supports_tools=True)

    async def list_tools(self):
        self.list_count += 1
        await asyncio.sleep(0)
        return (
            MCPToolDescriptor(
                tool_ref(self.server, "create"),
                self.server,
                "create",
                "Create objects",
                {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return MCPCallResult(
            content=(MCPContent("text", "created"),),
            structured_content={"count": arguments["count"]},
        )

    async def close(self):
        self.closed = True


def validate_arguments(descriptor, arguments):
    del descriptor
    if not isinstance(arguments.get("count"), int):
        raise ValueError("$.count: must be an integer")


def make_manager(*, failing=()):
    configs = parse_mcp_servers(
        {
            "blender": {"url": "https://blender.example/mcp"},
            "browser": {"command": "browser-mcp"},
        }
    )
    backends = {}

    def factory(config, changed):
        backend = FakeBackend(config.name, changed, config.name in failing)
        backends[config.name] = backend
        return backend

    return (
        MCPClientManager(
            configs, workspace_path="/workspace", backend_factory=factory,
            argument_validator=validate_arguments,
        ),
        backends,
    )


def test_manager按需连接并复用catalog():
    manager, backends = make_manager()

    async def run():
        await manager.search_tools(query="create", server="blender", limit=5)
        await manager.search_tools(query="objects", server="blender", limit=5)
        await manager.close()

    asyncio.run(run())

    assert backends["blender"].connect_count == 1
    assert backends["blender"].list_count == 1
    assert "browser" not in backends
    assert backends["blender"].closed is True


def test_manager并发首次访问只连接和列举一次():
    manager, backends = make_manager()

    async def run():
        await asyncio.gather(
            manager.ensure_catalog("blender"),
            manager.ensure_catalog("blender"),
        )
        await manager.close()

    asyncio.run(run())

    assert backends["blender"].connect_count == 1
    assert backends["blender"].list_count == 1


def test_manager变化通知只标记并在下次访问刷新():
    manager, backends = make_manager()

    async def run():
        await manager.ensure_catalog("blender")
        backend = backends["blender"]
        backend.changed()
        assert backend.list_count == 1
        await manager.ensure_catalog("blender")
        assert backend.list_count == 2
        await manager.close()

    asyncio.run(run())


def test_manager刷新期间的变化通知不会被旧结果覆盖():
    manager, backends = make_manager()

    async def run():
        await manager.ensure_catalog("blender")
        backend = backends["blender"]
        original = backend.list_tools

        async def list_and_change():
            result = await original()
            backend.changed()
            return result

        backend.list_tools = list_and_change
        backend.changed()
        await manager.ensure_catalog("blender")
        assert backend.list_count == 2
        await manager.ensure_catalog("blender")
        assert backend.list_count == 3
        await manager.close()

    asyncio.run(run())


def test_manager多server部分失败仍返回成功结果():
    manager, _ = make_manager(failing={"browser"})

    async def run():
        tools, total, errors = await manager.list_tools(
            server=None, page=1, page_size=20
        )
        await manager.close()
        return tools, total, errors

    tools, total, errors = asyncio.run(run())
    assert total == 1
    assert tools[0].tool_ref == "blender/create"
    assert "ConnectionError: browser unavailable" in errors["browser"]


def test_manager执行前校验并原样传递参数():
    manager, backends = make_manager()

    async def run():
        with pytest.raises(ValueError, match="must be an integer"):
            await manager.call_tool("blender/create", {"count": "3"})
        result = await manager.call_tool("blender/create", {"count": 3})
        await manager.close()
        return result

    result = asyncio.run(run())
    assert result.structured_content == {"count": 3}
    assert backends["blender"].calls == [("create", {"count": 3})]


def test_manager调用失败不自动重放并在下次重新连接():
    manager, backends = make_manager()
    created = []

    class FailingCallBackend(FakeBackend):
        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            raise ConnectionError("connection closed")

    def factory(config, changed):
        backend = (
            FailingCallBackend(config.name, changed)
            if not created
            else FakeBackend(config.name, changed)
        )
        created.append(backend)
        backends[config.name] = backend
        return backend

    manager._backend_factory = factory
    manager._argument_validator = lambda descriptor, arguments: None

    async def run():
        with pytest.raises(ConnectionError, match="closed"):
            await manager.call_tool("blender/create", {"count": 1})
        assert created[0].calls == [("create", {"count": 1})]
        result = await manager.call_tool("blender/create", {"count": 2})
        await manager.close()
        return result

    result = asyncio.run(run())
    assert result.structured_content == {"count": 2}
    assert len(created) == 2


def test_manager清理失败不覆盖原始调用错误():
    manager, backends = make_manager()

    async def run():
        await manager.ensure_catalog("blender")
        backend = backends["blender"]

        async def fail_call(name, arguments):
            del name, arguments
            raise ConnectionError("original failure")

        async def fail_close():
            raise RuntimeError("cleanup failure")

        backend.call_tool = fail_call
        backend.close = fail_close
        with pytest.raises(ConnectionError, match="original failure") as error:
            await manager.call_tool("blender/create", {"count": 1})
        assert "cleanup failure" in " ".join(error.value.__notes__)

    asyncio.run(run())


def test_jsonschema_validator拒绝非法参数并接受嵌套参数():
    target = MCPToolDescriptor(
        "blender/create",
        "blender",
        "create",
        "Create",
        {
            "type": "object",
            "properties": {
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "count": {"type": "integer"},
            },
            "required": ["location", "count"],
            "additionalProperties": False,
        },
    )

    _validate_arguments(target, {"location": [1, 2, 0], "count": 3})
    with pytest.raises(ValueError, match=r"\$\.location: failed"):
        _validate_arguments(target, {"location": [1, 2], "count": "3"})


@pytest.mark.parametrize(
    "reference",
    [
        "https://127.0.0.1/schema.json",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
def test_jsonschema_validator拒绝外部引用(reference):
    target = MCPToolDescriptor(
        "remote/danger", "remote", "danger", "Danger",
        {"type": "object", "properties": {"value": {"$ref": reference}}},
    )

    with pytest.raises(ValueError, match="external reference"):
        _validate_arguments(target, {"value": "anything"})


def test_jsonschema_validator允许文档内引用():
    target = MCPToolDescriptor(
        "remote/safe", "remote", "safe", "Safe",
        {
            "type": "object",
            "properties": {"value": {"$ref": "#/$defs/value"}},
            "required": ["value"],
            "$defs": {"value": {"type": "string"}},
        },
    )

    _validate_arguments(target, {"value": "ok"})


def test_jsonschema_validator不把业务字段和示例中的ref当引用():
    target = MCPToolDescriptor(
        "remote/ref_field", "remote", "ref_field", "Ref field",
        {
            "type": "object",
            "properties": {
                "$ref": {"type": "string"},
                "payload": {
                    "type": "object",
                    "default": {"$ref": "ordinary-data"},
                },
            },
            "required": ["$ref"],
        },
    )

    _validate_arguments(target, {"$ref": "local-value"})


def test_manager取消调用时传播取消且不自动重放():
    manager, backends = make_manager()
    started = asyncio.Event()

    async def run():
        await manager.ensure_catalog("blender")
        backend = backends["blender"]

        async def blocked(name, arguments):
            backend.calls.append((name, dict(arguments)))
            started.set()
            await asyncio.Event().wait()

        backend.call_tool = blocked
        task = asyncio.create_task(
            manager.call_tool("blender/create", {"count": 1})
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.calls == [("create", {"count": 1})]
        assert backend.closed is False
        await manager.close()

    asyncio.run(run())
