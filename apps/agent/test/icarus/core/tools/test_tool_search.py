import asyncio

from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult, ToolRegistry
from icarus.core.tools.tool_search import ToolSearchTool
from icarus.core.tools.tool_surface import ToolSurfaceCatalog


class StubTool(BaseTool):
    def __init__(self, name: str, description: str) -> None:
        self._definition = ToolDefinition(
            name,
            description,
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "target path"}
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    @property
    def definition(self):
        return self._definition

    def invoke(self, arguments):
        return ToolExecutionResult(True, arguments)


def make_catalog() -> ToolSurfaceCatalog:
    registry = ToolRegistry()
    registry.register_many(
        [
            StubTool("one", "Use one when reading alpha data. Example: path='a'."),
            StubTool("two", "Use two when writing alpha data. Example: path='b'."),
            StubTool("three", "Use three for beta data. Example: path='c'."),
        ]
    )
    return ToolSurfaceCatalog.build(
        [("two", "alpha"), ("one", "alpha"), ("three", "beta")],
        registry,
    )


def test_tool_search命中功能面返回完整定义和加载metadata():
    tool = ToolSearchTool(make_catalog())

    result = tool.invoke({"query": "alpha"})

    assert result.success is True
    assert result.metadata["tools_loaded"] == ["one", "two"]
    assert "one" in result.output
    assert "Use one when reading alpha data. Example: path='a'." in result.output
    assert '"required": ["path"]' in result.output
    assert '"additionalProperties": false' in result.output
    assert "_execution" not in result.output
    assert "tool_execute" in result.output


def test_tool_search未命中和空query返回目录且不加载():
    tool = ToolSearchTool(make_catalog())

    missing = tool.invoke({"query": "unknown"})
    empty = tool.invoke({})

    for result in (missing, empty):
        assert result.success is True
        assert "tools_loaded" not in result.metadata
        assert "alpha(2): one, two" in result.output
        assert "beta(1): three" in result.output


def test_tool_search定义目录随catalog绑定更新且支持并行():
    tool = ToolSearchTool()
    assert "alpha(2)" not in tool.definition.description

    tool.bind_catalog(make_catalog())

    assert "alpha(2): one, two" in tool.definition.description
    assert "beta(1): three" in tool.definition.description
    assert "tool_execute" in tool.definition.description
    assert tool.can_run_parallel({"query": "alpha"}) is True
    assert tool.definition.input_schema["additionalProperties"] is False


def test_tool_search参数错误和异步行为与同步一致():
    tool = ToolSearchTool(make_catalog())

    unknown = tool.invoke({"query": "alpha", "extra": True})
    invalid = tool.invoke({"query": 1})
    synced = tool.invoke({"query": "alpha"})
    asynced = asyncio.run(tool.ainvoke({"query": "alpha"}))

    assert unknown.success is False
    assert "unknown arguments: extra" in unknown.error
    assert invalid.success is False
    assert "query must be a string" in invalid.error
    assert asynced.success == synced.success
    assert asynced.output == synced.output
    assert asynced.metadata["tools_loaded"] == synced.metadata["tools_loaded"]
