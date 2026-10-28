from dataclasses import FrozenInstanceError

import pytest

from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult, ToolRegistry
from icarus.core.tools.tool_surface import ToolSurfaceCatalog


class StubTool(BaseTool):
    def __init__(self, name: str, description: str = "stub") -> None:
        self._definition = ToolDefinition(
            name,
            description,
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
        )

    @property
    def definition(self):
        return self._definition

    def invoke(self, arguments):
        return ToolExecutionResult(True, arguments)


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(
        [
            StubTool("read"),
            StubTool("tool_search"),
            StubTool("one", "first tool"),
            StubTool("two", "second tool"),
            StubTool("three", "third tool"),
        ]
    )
    return registry


def test_tool_surface按plugin精确查组并排除常驻工具():
    registry = make_registry()
    catalog = ToolSurfaceCatalog.build(
        [
            ("read", "builtin-tools"),
            ("tool_search", "session"),
            ("two", "alpha"),
            ("one", "alpha"),
            ("three", "beta"),
        ],
        registry,
    )

    assert [item.name for item in catalog.search("alpha")] == ["one", "two"]
    assert [item.name for item in catalog.search("beta")] == ["three"]
    assert catalog.search("builtin-tools") == ()
    assert catalog.search("session") == ()
    assert catalog.search("missing") == ()
    assert catalog.directory() == (
        "alpha(2): one, two",
        "beta(1): three",
    )


def test_tool_surface保留registry原始定义且快照不可变():
    registry = make_registry()
    original = registry.get("one").definition
    catalog = ToolSurfaceCatalog.build([("one", "alpha")], registry)

    original.input_schema["properties"]["value"]["type"] = "integer"
    first = catalog.search("alpha")[0]
    assert first.description == "first tool"
    assert first.input_schema["properties"]["value"]["type"] == "string"
    assert "_execution" not in first.input_schema["properties"]

    first.input_schema["properties"]["value"]["type"] = "number"
    second = catalog.search("alpha")[0]
    assert second.input_schema["properties"]["value"]["type"] == "string"
    assert isinstance(catalog.entries, tuple)
    exposed = catalog.entries[0]
    exposed.definition.input_schema["properties"]["value"]["type"] = "boolean"
    assert catalog.search("alpha")[0].input_schema["properties"]["value"]["type"] == "string"
    with pytest.raises(FrozenInstanceError):
        catalog.entries[0].name = "changed"
