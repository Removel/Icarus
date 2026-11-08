from agent_app.contracts.prompt import PromptContributionEvent
from agent_app.plugins.skill.models import SkillDefinition
from agent_app.plugins.skill.prompts import discovery_guidance
from icarus.core.contracts.input import UserInputEvent
from apps.agent.test.agent_app.plugins.skill.test_plugin import plugin


def test_skill_discovery_is_static_but_catalog_and_write_policy_are_deferred(tmp_path):
    skills = [SkillDefinition(f"skill-{i:02}", "fixture skill", tmp_path / f"skill-{i:02}" / "SKILL.md", "workspace") for i in range(20)]
    target, jobs = plugin(tmp_path, skills=skills, allow_produce=False, allow_evolve=False)
    def unexpected_scan(*args, **kwargs):
        raise AssertionError("Static guidance must not scan a Skill catalog")
    target.catalog.list_skills = unexpected_scan
    sections = target.static_prompt_sections()
    assert len(sections) == 1
    assert sections[0].layer == "session"
    content = sections[0].content
    assert "skills_list" in content and "skill_search" in content
    assert "SKILL.md" in content and "read" in content
    assert "skill-00" not in content and "skill-08" not in content
    assert len(content) < 2000
    assert "untrusted" in content
    assert "writes are disabled" in target.tool_scope_guidance()
    assert not target.accepts_event("user-input", UserInputEvent(task_id="task", prompt="help"))
    assert not jobs.produce_calls and not jobs.evolve_calls


def test_evolve_only_guidance_requires_job_verification():
    text = discovery_guidance(allow_produce=False, allow_evolve=True)
    assert "skill_evolve is enabled" in text
    assert "skill_produce is enabled" not in text
    assert "Submission is not completion; verify with skill_job_status" in text
    assert "does not prove absence" in text
