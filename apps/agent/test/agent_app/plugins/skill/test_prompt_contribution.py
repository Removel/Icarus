from agent_app.contracts.prompt import PromptContributionEvent
from agent_app.plugins.skill.models import SkillDefinition
from icarus.core.contracts.input import UserInputEvent
from apps.agent.test.agent_app.plugins.skill.test_plugin import plugin


def test_skill_guidance_is_static_session_with_bounded_catalog(tmp_path):
    skills = [SkillDefinition(f"skill-{i:02}", "fixture skill", tmp_path / f"skill-{i:02}" / "SKILL.md", "workspace") for i in range(20)]
    target, jobs = plugin(tmp_path, skills=skills, allow_produce=False, allow_evolve=False)
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "skills_list" in content and "skill_search" in content
    assert "SKILL.md" in content and "读取" in content
    assert "skill-00" in content and "skill-08" not in content
    assert len(content) < 2000
    assert "不可信" in content
    assert "当前未开放" in content
    assert not target.accepts_event("user-input", UserInputEvent(task_id="task", prompt="help"))
    assert not jobs.produce_calls and not jobs.evolve_calls
