import pytest

from agent_app.plugins.mcp.plugin import MCPPlugin
from apps.agent.test.agent_app.plugins.mcp.test_plugin import BridgeStub, EmptyManagerStub, ManagerStub


@pytest.mark.parametrize("configured", [True, False])
def test_mcp_guidance_is_static_session_and_configuration_only(configured):
    bridge = BridgeStub()
    target = MCPPlugin("mcp", manager=ManagerStub() if configured else EmptyManagerStub(), bridge=bridge)
    sections = target.static_prompt_sections()
    if configured:
        assert len(sections) == 1
        assert sections[0].layer == "session"
        content = sections[0].content
        assert "mcp_tool_search" in content
        assert "tool_ref" in content
        assert "不代表" in content
    else:
        assert sections == ()
    assert not bridge.is_running
