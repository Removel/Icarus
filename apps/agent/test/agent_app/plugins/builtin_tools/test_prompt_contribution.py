from agent_app.plugins.builtin_tools.plugin import BuiltinToolsPlugin


def test_builtin_guidance_is_static_session():
    target = BuiltinToolsPlugin("builtin-tools")
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "read" in content and "files" in content
    assert "write/insert" in content
    assert "bash" in content and "exit codes" in content
