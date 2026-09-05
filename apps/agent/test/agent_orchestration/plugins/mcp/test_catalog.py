from apps.agent.src.agent_orchestration.plugins.mcp.catalog import (
    MCPToolCatalog,
    tool_ref,
)
from apps.agent.src.agent_orchestration.plugins.mcp.models import (
    MCPToolDescriptor,
)


def descriptor(server, name, description="", title=None, schema=None):
    return MCPToolDescriptor(
        tool_ref=tool_ref(server, name),
        server=server,
        name=name,
        title=title,
        description=description,
        input_schema=schema or {"type": "object"},
    )


def test_catalog原子替换并只在内容变化时增加generation():
    catalog = MCPToolCatalog()
    first = catalog.replace_server(
        "blender", [descriptor("blender", "create_cube")]
    )
    same = catalog.replace_server(
        "blender", [descriptor("blender", "create_cube")]
    )
    changed = catalog.replace_server(
        "blender", [descriptor("blender", "delete_cube")]
    )

    assert first.generation == 1
    assert same is first
    assert changed.generation == 2
    assert list(changed.by_ref) == ["blender/delete_cube"]


def test_catalog稳定排序和分页():
    catalog = MCPToolCatalog()
    catalog.replace_server(
        "blender",
        [descriptor("blender", "z_tool"), descriptor("blender", "a_tool")],
    )
    catalog.replace_server("browser", [descriptor("browser", "click")])

    first, total = catalog.list(page=1, page_size=2)
    second, _ = catalog.list(page=2, page_size=2)

    assert total == 3
    assert [item.tool_ref for item in first] == [
        "blender/a_tool",
        "blender/z_tool",
    ]
    assert [item.tool_ref for item in second] == ["browser/click"]


def test_catalog_search按名称标题描述和server确定性排序():
    catalog = MCPToolCatalog()
    catalog.replace_server(
        "blender",
        [
            descriptor("blender", "create_objects", "Create many meshes"),
            descriptor("blender", "inspect_scene", "Inspect Blender objects"),
        ],
    )
    catalog.replace_server(
        "browser",
        [descriptor("browser", "create_tab", "Open a browser tab")],
    )

    assert [item.tool_ref for item in catalog.search("create", limit=3)] == [
        "blender/create_objects",
        "browser/create_tab",
    ]
    assert [
        item.tool_ref
        for item in catalog.search("objects", server="blender")
    ] == ["blender/create_objects", "blender/inspect_scene"]


def test_catalog复制schema避免调用方原地修改snapshot():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
    catalog = MCPToolCatalog()
    catalog.replace_server("blender", [descriptor("blender", "create", schema=schema)])
    schema["properties"]["count"]["type"] = "string"

    stored = catalog.get("blender/create")
    assert stored is not None
    assert stored.input_schema["properties"]["count"]["type"] == "integer"
    try:
        stored.input_schema["properties"]["count"]["type"] = "number"
    except TypeError:
        pass
    else:
        raise AssertionError("nested Catalog mappings must be immutable")
    assert stored.as_dict()["input_schema"]["properties"]["count"]["type"] == "integer"
