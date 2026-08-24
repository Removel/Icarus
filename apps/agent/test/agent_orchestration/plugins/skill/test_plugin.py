import asyncio
from dataclasses import replace
import inspect
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.catalog import SkillCatalog
from apps.agent.src.agent_orchestration.plugins.skill.jobs import SkillJob
from apps.agent.src.agent_orchestration.plugins.skill.models import SkillDefinition
from apps.agent.src.agent_orchestration.plugins.skill.plugin import (
    SkillOperationError,
    SkillPlugin,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
)
from apps.agent.src.agent_orchestration.plugins.skill.scanner import SkillScanner
from apps.agent.src.agent_orchestration.run_control import (
    TaskContextInputResultEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart


class CatalogStub:
    def __init__(self, skills=()):
        self.skills = list(skills)

    def list_skills(self, scope="all"):
        return [skill for skill in self.skills if scope == "all" or skill.scope == scope]

    def search(self, keywords):
        return list(self.skills)

    def find_visible(self, name):
        normalized = name.strip().casefold()
        return next((skill for skill in self.skills if skill.normalized_name == normalized), None)


class RepositoryStub:
    def __init__(self, snapshot=None, conflicts=()):
        self.snapshot = snapshot
        self.conflicts = tuple(conflicts)

    def find_conflicts(self, name):
        return self.conflicts

    def capture(self, name):
        return self.snapshot


class ConversationStub:
    def __init__(self, messages=()):
        self.messages = list(messages)

    def get_messages(self):
        return list(self.messages)


class JobManagerStub:
    def __init__(self):
        self.produce_calls = []
        self.evolve_calls = []
        self.notification_events = []
        self.jobs = {}
        self.lifecycle = []

    def bind_publisher(self, publisher):
        self.publisher = publisher

    async def start(self): self.lifecycle.append("start")
    async def quiesce(self): self.lifecycle.append("quiesce")
    async def drain(self): self.lifecycle.append("drain")
    async def stop(self): self.lifecycle.append("stop")

    def submit_produce(self, **kwargs):
        self.produce_calls.append(kwargs)
        job = make_job(operation="produce", scope=kwargs["scope"])
        self.jobs[job.job_id] = job
        return job

    def submit_evolve(self, **kwargs):
        self.evolve_calls.append(kwargs)
        job = make_job(operation="evolve", scope=None)
        self.jobs[job.job_id] = job
        return job

    def get(self, job_id): return self.jobs.get(job_id)
    def record_notification_result(self, event):
        self.notification_events.append(event)
    async def restore_workspace_state(self, state, *, state_version):
        self.workspace_restore = (state, state_version)
    async def restore_session_state(self, state, *, state_version):
        self.session_restore = (state, state_version)
    async def snapshot_workspace_state(self): return {"jobs": []}
    async def snapshot_session_state(self): return {"job_ids": []}


def make_job(operation="produce", scope="workspace"):
    return SkillJob.create(
        job_id=f"job-{operation}", operation=operation, target_name="sample", scope=scope,
        task_id="task", run_id="run", step=1
    )


def skill(tmp_path, name="sample", scope="workspace"):
    return SkillDefinition(
        name=name, description="description", scope=scope, path=tmp_path / name / "SKILL.md"
    )


def plugin(tmp_path, *, allow_produce=True, allow_evolve=True, skills=(), conflicts=(), snapshot=None):
    jobs = JobManagerStub()
    instance = SkillPlugin(
        "skill", catalog=CatalogStub(skills),
        repository=RepositoryStub(snapshot, conflicts),
        job_manager=jobs, conversation=ConversationStub([Message("user", [TextPart("history")])]),
        allow_produce=allow_produce, allow_evolve=allow_evolve,
    )
    return instance, jobs


def execution():
    return {
        "task_id": "task", "run_id": "run", "step": 2,
        "task_messages": (Message("assistant", [TextPart("current")]),),
    }


def test_list_search_and_job_status_return_public_fields_only(tmp_path):
    item = skill(tmp_path)
    instance, jobs = plugin(tmp_path, skills=[item])
    finished = make_job().transition("running").transition("succeeded", path=tmp_path / "SKILL.md")
    jobs.jobs[finished.job_id] = finished

    assert instance.list_skills() == [{
        "name": "sample", "description": "description", "scope": "workspace",
        "path": str(item.path),
    }]
    assert instance.search(["sample"])[0]["name"] == "sample"
    status = instance.job_status(finished.job_id)
    assert status["status"] == "succeeded"
    assert "conversation" not in status
    assert "instructions" not in status
    with pytest.raises(SkillOperationError, match="not found"):
        instance.job_status("missing")


def test_plugin_method_annotations_can_be_evaluated():
    assert inspect.signature(SkillPlugin.search).return_annotation == list[
        dict[str, str]
    ]


def test_produce_precheck_and_context_assembly(tmp_path):
    instance, jobs = plugin(tmp_path)

    result = instance.produce(name="sample", scope="global", instructions="make it", **execution())

    assert result == {"job_id": "job-produce", "status": "queued"}
    call = jobs.produce_calls[0]
    assert [message.content[0].text for message in call["conversation"]] == ["history", "current"]
    assert call["scope"] == "global"

    conflict, conflict_jobs = plugin(tmp_path, conflicts=["workspace"])
    with pytest.raises(SkillOperationError) as error:
        conflict.produce(name="sample", scope="workspace", instructions="x", **execution())
    assert error.value.code == "target_conflict"
    assert conflict_jobs.produce_calls == []


def test_produce_physical_conflict_fails_before_job_even_if_skill_is_invalid(
    tmp_path,
):
    global_dir = tmp_path / "global"
    workspace_dir = tmp_path / "workspace"
    occupied = workspace_dir / "occupied"
    occupied.mkdir(parents=True)
    (occupied / "SKILL.md").write_text("invalid", encoding="utf-8")
    jobs = JobManagerStub()
    instance = SkillPlugin(
        "skill",
        catalog=SkillCatalog(SkillScanner(global_dir, workspace_dir)),
        repository=SkillRepository(global_dir, workspace_dir),
        job_manager=jobs,
        conversation=ConversationStub(),
        allow_produce=True,
    )

    with pytest.raises(SkillOperationError) as error:
        instance.produce(
            name="occupied",
            scope="global",
            instructions="create it",
            **execution(),
        )

    assert error.value.code == "target_conflict"
    assert jobs.produce_calls == []


def test_evolve_captures_visible_skill_and_context(tmp_path):
    visible = skill(tmp_path, scope="global")
    snapshot = object()
    instance, jobs = plugin(tmp_path, skills=[visible], snapshot=snapshot)

    result = instance.evolve(name="SAMPLE", instructions="improve", **execution())

    assert result["status"] == "queued"
    assert jobs.evolve_calls[0]["snapshot"] is snapshot
    assert len(jobs.evolve_calls[0]["conversation"]) == 2


def test_write_permissions_and_task_context_fail_before_job_creation(tmp_path):
    disabled, jobs = plugin(tmp_path, allow_produce=False, allow_evolve=False)
    with pytest.raises(SkillOperationError) as error:
        disabled.produce(name="x", scope="workspace", instructions="x", **execution())
    assert error.value.code == "disabled_by_policy"

    enabled, enabled_jobs = plugin(tmp_path)
    invalid = execution() | {"task_messages": ()}
    with pytest.raises(SkillOperationError) as error:
        enabled.produce(name="x", scope="workspace", instructions="x", **invalid)
    assert error.value.code == "invalid_task_context"
    assert jobs.produce_calls == enabled_jobs.produce_calls == []


def test_plugin_only_consumes_agent_notification_results(tmp_path):
    instance, jobs = plugin(tmp_path)
    event = TaskContextInputResultEvent(
        task_id="task", request_event_id="event", status="accepted"
    )

    assert instance.accepts_event("agent", event) is True
    assert instance.accepts_event("other", event) is False
    asyncio.run(instance.consume("agent", event))
    assert jobs.notification_events == [event]


def test_plugin_delegates_lifecycle_and_state(tmp_path):
    async def run():
        instance, jobs = plugin(tmp_path)
        await instance.start()
        await instance.restore_workspace_state({"jobs": []}, state_version=1)
        await instance.restore_session_state({"job_ids": []}, state_version=1)
        assert await instance.snapshot_workspace_state() == {"jobs": []}
        assert await instance.snapshot_session_state() == {"job_ids": []}
        await instance.quiesce()
        await instance.drain()
        await instance.stop()
        return jobs

    jobs = asyncio.run(run())
    assert jobs.lifecycle == ["start", "quiesce", "drain", "stop"]
