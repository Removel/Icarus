import json
from pathlib import Path

from agent_app.plugins.builtin_tools.factory import create_plugin
from icarus.core.registration import PluginRegistration
from icarus.core.tools.tool_checker import ToolChecker


MANIFEST_PATH = Path(
    "apps/agent/src/agent_app/plugins/builtin_tools/manifest.json"
)


def registration(config=None):
    return create_plugin(
        "builtin-tools", Path("."), "session", config or {}, {}, None,
    )


def test_factory工具与manifest声明一致():
    manifest = json.loads(MANIFEST_PATH.read_text())
    value = registration()

    assert isinstance(value, PluginRegistration)
    tools = value.plugin.build_tools()
    assert [tool.definition.name for tool in tools] == manifest["provided_tools"]
    assert all(ToolChecker().check(tool).valid for tool in tools)


def test_factory应用工具配置():
    tools = {
        tool.definition.name: tool
        for tool in registration({
            "max_output_bytes": 4096,
            "default_read_lines": 12,
            "max_read_lines": 34,
        }).plugin.build_tools()
    }

    assert tools["read"].default_limit == 12
    assert tools["read"].maximum_limit == 34
    assert tools["bash"].max_output_bytes == 4096
