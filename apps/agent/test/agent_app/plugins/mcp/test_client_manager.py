import asyncio
from dataclasses import dataclass

import pytest

from agent_app.plugins.mcp.catalog import tool_ref
from agent_app.plugins.mcp.client_manager import (
    MCPClientManager,
)
from agent_app.plugins.mcp.config import (
    parse_mcp_servers,
)
from agent_app.plugins.mcp.models import (
    MCPCallResult,
    MCPContent,
    MCPServerInfo,
    MCPToolDescriptor,
)
from agent_app.plugins.mcp.client_manager import (
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

    async def call_tool(self, name, arguments, *, timeout_seconds=None):
        self.calls.append((name, dict(arguments), timeout_seconds))
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
    assert backends["blender"].calls == [("create", {"count": 3}, None)]


def test_manager透传每次调用的framework_timeout():
    manager, backends = make_manager()

    async def run():
        result = await manager.call_tool(
            "blender/create", {"count": 3}, timeout_seconds=45
        )
        await manager.close()
        return result

    result = asyncio.run(run())

    assert result.structured_content == {"count": 3}
    assert backends["blender"].calls == [("create", {"count": 3}, 45)]


def test_manager调用失败不自动重放并在下次重新连接():
    manager, backends = make_manager()
    created = []

    class FailingCallBackend(FakeBackend):
        async def call_tool(self, name, arguments, *, timeout_seconds=None):
            self.calls.append((name, dict(arguments), timeout_seconds))
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
        assert created[0].calls == [("create", {"count": 1}, None)]
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

        async def fail_call(name, arguments, *, timeout_seconds=None):
            del name, arguments, timeout_seconds
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


@pytest.mark.parametrize("schema, arguments, expected", [
    ({"type": "object", "required": ["channel"]}, {}, "$.channel: missing required key"),
    ({"type": "object", "properties": {"payload": {
        "type": "object", "required": ["channel"],
    }}}, {"payload": {}}, "$.payload.channel: missing required key"),
    ({"type": "object", "additionalProperties": False},
     {"unexpected": "synthetic-argument-secret"}, "$.unexpected: unknown key"),
    ({"type": "object", "properties": {"count": {"type": "integer"}}},
     {"count": "synthetic-argument-secret"}, "$.count: expected type integer"),
    ({"type": "object", "properties": {"mode": {"enum": ["synthetic-schema-secret"]}}},
     {"mode": "synthetic-argument-secret"}, "$.mode: expected a declared enum value"),
    ({"type": "object", "properties": {"rows": {
        "type": "array", "items": {"type": "integer"},
    }}}, {"rows": ["synthetic-argument-secret"]}, "$.rows[0]: expected type integer"),
])
def test_jsonschema_validator提供精确有界诊断且不回显值(schema, arguments, expected, caplog):
    import traceback

    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", schema)
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, arguments)
    message = str(caught.value)
    assert expected in message
    diagnostic_is_safe = all(secret not in message + caplog.text + "".join(
        traceback.format_exception(caught.value)
    ) for secret in ("synthetic-argument-secret", "synthetic-schema-secret"))
    assert diagnostic_is_safe


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_jsonschema_validator可空类型显示替代类型且不泄漏值(keyword, caplog):
    import traceback

    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "object", "properties": {
            "count": {keyword: [{"type": "integer"}, {"type": "null"}]},
        },
    })
    arguments = {"count": "synthetic-union-secret"}
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, arguments)
    message = str(caught.value)
    assert f"{keyword} alternative 1: $.count: expected type integer" in message
    assert f"{keyword} alternative 2: $.count: expected type null" in message
    diagnostic_is_safe = "synthetic-union-secret" not in (
        message + caplog.text + "".join(traceback.format_exception(caught.value))
    )
    assert diagnostic_is_safe
    _validate_arguments(target, {"count": 3})
    _validate_arguments(target, {"count": None})


def test_jsonschema_validator嵌套替代保留字段诊断且不泄漏enum或参数(caplog):
    import traceback

    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "object", "properties": {"config": {"oneOf": [
            {"type": "object", "properties": {
                "mode": {"anyOf": [
                    {"enum": ["synthetic-enum-secret"]}, {"type": "null"},
                ]},
            }, "additionalProperties": False},
            {"type": "null"},
        ]}},
    })
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, {"config": {
            "mode": "synthetic-union-secret", "extra": "synthetic-extra-secret",
        }})
    message = str(caught.value)
    assert "oneOf alternative 1: anyOf alternative 1: $.config.mode: expected a declared enum value" in message
    assert "oneOf alternative 1: $.config.extra: unknown key" in message
    assert "oneOf alternative 2: $.config: expected type null" in message
    diagnostic_is_safe = all(secret not in (
        message + caplog.text + "".join(traceback.format_exception(caught.value))
    ) for secret in ("synthetic-enum-secret", "synthetic-union-secret", "synthetic-extra-secret"))
    assert diagnostic_is_safe


def test_jsonschema_validator替代诊断仍受全局八条和长度限制():
    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "object", "anyOf": [
            {"required": ["x" * 1000 + str(index)]} for index in range(20)
        ],
    })
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, {})
    message = str(caught.value)
    assert message.count("anyOf alternative") == 8
    assert len(message) <= 2200


def test_jsonschema_validator替代递归在有限深度保留安全降级():
    schema = {"type": "integer"}
    for _ in range(12):
        schema = {"anyOf": [schema, {"type": "null"}]}
    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "object", "properties": {"count": schema},
    })
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, {"count": "synthetic-union-secret"})
    message = str(caught.value)
    assert "anyOf alternative" in message
    assert "failed anyOf validation" in message
    assert message.count("anyOf alternative") <= 16
    assert len(message) <= 2200


def test_jsonschema_validator最多8条诊断且字段长度有界():
    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "object", "required": [f"missing_{index}" for index in range(20)],
        "additionalProperties": False,
    })
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, {"x" * 10_000: "unused"})
    message = str(caught.value)
    assert message.count("missing required key") == 8
    assert len(message) <= 2200


@pytest.mark.parametrize("schema, arguments", [
    ({"type": "object", "properties": {"value": {"type": ["integer"] * 1000}}}, {"value": "invalid"}),
    ({"type": "object", "additionalProperties": False}, {"x" * 10_000: "invalid"}),
])
def test_jsonschema_validator单条诊断也有界(schema, arguments):
    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", schema)
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, arguments)
    assert len(str(caught.value)) <= 500


def test_jsonschema_validator非法schema不泄漏任意schema值(caplog):
    import traceback

    target = MCPToolDescriptor("remote/test", "remote", "test", "Test", {
        "type": "synthetic-schema-secret",
    })
    with pytest.raises(ValueError) as caught:
        _validate_arguments(target, {})
    diagnostic_is_safe = "synthetic-schema-secret" not in (
        "".join(traceback.format_exception(caught.value)) + caplog.text
    )
    assert diagnostic_is_safe
    assert "invalid input schema" in str(caught.value)


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

        async def blocked(name, arguments, *, timeout_seconds=None):
            backend.calls.append((name, dict(arguments), timeout_seconds))
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
        assert backend.calls == [("create", {"count": 1}, None)]
        assert backend.closed is False
        await manager.close()

    asyncio.run(run())
