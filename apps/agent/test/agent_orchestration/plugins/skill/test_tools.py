from apps.agent.src.agent_orchestration.plugins.skill.plugin import SkillOperationError
from apps.agent.src.agent_orchestration.plugins.skill.tools import create_skill_tools
from apps.agent.src.model_provider.types import Message, TextPart


class PluginStub:
    def list_skills(self, scope): return [{"name": scope}]
    def search(self, keywords): return [{"name": keywords[0]}]
    def produce(self, **kwargs):
        self.produce_call = kwargs
        return {"job_id": "produce", "status": "queued"}
    def evolve(self, **kwargs):
        self.evolve_call = kwargs
        return {"job_id": "evolve", "status": "queued"}
    def job_status(self, job_id):
        if job_id == "missing":
            raise SkillOperationError("job_not_found", "missing")
        return {"job_id": job_id, "status": "running"}


def by_name():
    plugin = PluginStub()
    tools = {tool.definition.name: tool for tool in create_skill_tools(plugin)}
    return plugin, tools


def test_tool_names_schemas_and_parallel_contract():
    _, tools = by_name()
    assert list(tools) == [
        "skills_list", "skill_search", "skill_produce", "skill_evolve", "skill_job_status"
    ]
    assert all(tool.definition.input_schema["additionalProperties"] is False for tool in tools.values())
    assert [tools[name].can_run_parallel({}) for name in tools] == [True, True, False, False, True]


def test_read_tools_validate_and_return_structured_results():
    _, tools = by_name()
    assert tools["skills_list"].invoke({}).output == {"skills": [{"name": "all"}]}
    assert tools["skill_search"].invoke({"keywords": ["python"]}).output == {"skills": [{"name": "python"}]}
    assert tools["skill_search"].invoke({"keywords": "python"}).success is False
    assert tools["skills_list"].invoke({"scope": "bad"}).success is False
    assert tools["skill_job_status"].invoke({"job_id": "missing"}).error.startswith("job_not_found:")


def test_write_tools_forward_execution_context_without_copying_messages():
    plugin, tools = by_name()
    messages = (Message("user", [TextPart("current")]),)
    execution = {"task_id": "task", "run_id": "run", "step": 3, "task_messages": messages}

    produced = tools["skill_produce"].invoke(
        {"name": "new", "scope": "workspace", "instructions": "create"}, **execution
    )
    evolved = tools["skill_evolve"].invoke(
        {"name": "old", "instructions": "improve"}, **execution
    )

    assert produced.success is evolved.success is True
    assert plugin.produce_call["task_messages"] is messages
    assert plugin.evolve_call["task_messages"] is messages
    assert plugin.produce_call["scope"] == "workspace"


def test_tools_reject_unknown_missing_and_empty_arguments():
    _, tools = by_name()
    assert tools["skills_list"].invoke({"extra": True}).success is False
    assert tools["skill_produce"].invoke({"name": "x"}).success is False
    assert tools["skill_evolve"].invoke({"name": " ", "instructions": "x"}).success is False
