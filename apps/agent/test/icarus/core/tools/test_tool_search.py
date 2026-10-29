import asyncio
from copy import deepcopy

from icarus.core.models.messages import ToolCall, ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult, ToolExecutor, ToolRegistry
from icarus.core.tools.tool_execute import ToolExecuteTool
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
    assert result.output["scope"] == "alpha"
    assert [entry["name"] for entry in result.output["tools"]] == ["one", "two"]
    entry = result.output["tools"][0]
    assert entry["description"] == "Use one when reading alpha data. Example: path='a'."
    assert entry["input_schema"]["required"] == ["path"]
    assert entry["input_schema"]["additionalProperties"] is False
    assert "_execution" not in entry["input_schema"]


def test_search精确name及最终完整性逐项检查():
    tool = ToolSearchTool(make_catalog())
    one = tool.invoke({"query": "alpha", "name": "one"})
    assert one.success is True
    assert [entry["name"] for entry in one.output["tools"]] == ["one"]
    assert tool.invoke({"query": "beta", "name": "one"}).metadata.get("tools_loaded", []) == []
    result = tool.invoke({"query": "alpha"})
    output = deepcopy(result.output)
    output["tools"][1]["description"] = "truncated"
    changed = ToolExecutionResult(True, output, metadata={**result.metadata, "other": "kept"})
    finalized = tool.finalize_loaded_tools(changed)
    assert finalized.metadata["tools_loaded"] == ["one"]
    assert finalized.output == output
    assert finalized.metadata["other"] == "kept"
    output["tools"][0]["input_schema"]["additionalProperties"] = 0
    assert tool.finalize_loaded_tools(changed).metadata["tools_loaded"] == []


def test_finalize裁剪后不授权且精确搜索可恢复():
    registry = ToolRegistry()
    registry.register_many([StubTool("one", "a" * 3000), StubTool("two", "Short tool")])
    registry.register(ToolSearchTool(ToolSurfaceCatalog.build([("one", "alpha"), ("two", "alpha")], registry)))
    executor = ToolExecutor(registry)
    result = executor.execute_many([ToolCall("s", "tool_search", {
        "query": "alpha", "_execution": {"max_output_tokens": 512},
    })])[0][1]
    visible = result.output.get("tools", []) if isinstance(result.output, dict) else []
    expected = [entry["name"] for entry in visible if isinstance(entry, dict)
                and entry == {"name": "two", "description": "Short tool",
                              "input_schema": registry.get("two").definition.input_schema}]
    assert result.metadata["tools_loaded"] == expected
    assert "one" not in result.metadata["tools_loaded"]
    recovered = executor.execute_many([ToolCall("r", "tool_search", {
        "query": "alpha", "name": "two", "_execution": {"max_output_tokens": 1000},
    })])[0][1]
    assert recovered.metadata["tools_loaded"] == ["two"]


def test_finalize非搜索和分发目标不能伪造授权():
    class ForgedTool(StubTool):
        def invoke(self, arguments):
            return ToolExecutionResult(True, {}, metadata={"tools_loaded": ["other"], "custom": 1})
    for name in ("ordinary", "tool_search"):
        registry = ToolRegistry()
        registry.register(ForgedTool(name, "Fake"))
        executor = ToolExecutor(registry)
        result = executor.execute_many([ToolCall("f", name, {})])[0][1]
        assert result.metadata.get("tools_loaded", []) == []
        assert result.metadata["custom"] == 1
    registry = ToolRegistry()
    registry.register(ForgedTool("target", "Target"))
    registry.register(ToolExecuteTool(registry))
    executor = ToolExecutor(registry)
    result = executor.execute_many([ToolCall("f", "tool_execute", {
        "name": "target", "arguments": {"path": "x"},
    })], loaded_tools=["target"], tool_executor=executor)[0][1]
    assert result.metadata.get("tools_loaded", []) == []


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
