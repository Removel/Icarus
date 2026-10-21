from agent_app.plugins.builtin_tools.plugin import BuiltinToolsPlugin


def test_builtin_guidance_is_static_session():
    target = BuiltinToolsPlugin("builtin-tools")
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "read" in content and "分页" in content
    assert "write" in content and "覆盖" in content
    assert "insert" in content and "行前" in content
    assert "bash" in content and "退出码" in content
