from icarus.core.contracts.input import UserInputEvent
from apps.agent.test.agent_app.plugins.knowledge.test_plugin import make_plugin


def test_knowledge_guidance_is_static_session_and_does_not_query(tmp_path):
    backend, target = make_plugin(tmp_path)
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "configured knowledge base" in content
    guidance = target.tool_scope_guidance()
    assert "knowledge_query" in guidance and "knowledge_read" in guidance
    assert "Cite actual returned sources" in guidance
    assert backend.calls == []
    assert not target.accepts_event("user-input", UserInputEvent(task_id="task", prompt="help"))
