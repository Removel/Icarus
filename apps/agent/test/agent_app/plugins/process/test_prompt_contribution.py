from apps.agent.test.agent_app.plugins.process.test_plugin import create_runtime


def test_process_guidance_is_static_session_and_does_not_start_a_process(tmp_path):
    target, runtime, events = create_runtime(tmp_path)
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "background_process" in content
    assert "ready" in content
    assert "logs" in content
    assert events == []
    assert not target._started
