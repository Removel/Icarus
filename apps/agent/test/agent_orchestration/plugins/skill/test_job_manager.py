import asyncio
from pathlib import Path
from threading import Event

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    SkillWriteCoordinator,
)
from apps.agent.src.agent_orchestration.plugins.skill.job_manager import (
    SkillJobManager,
)
from apps.agent.src.agent_orchestration.plugins.skill.jobs import SkillJob
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    SkillRepository,
    SkillSnapshot,
)
from apps.agent.src.agent_orchestration.hooks import get_hook_context
from apps.agent.src.agent_orchestration.run_control import (
    TaskContextInputEvent,
    TaskContextInputResultEvent,
)
from apps.agent.src.model_provider.types import Message, TextPart


def skill_content(name, body="body"):
    return f"---\nname: {name}\ndescription: test {name}\n---\n{body}\n"


class ProducerStub:
    def __init__(self, content=None, error=None, gate=None):
        self.content = content
        self.error = error
        self.gate = gate
        self.calls = []

    async def produce(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if self.content is not None:
            Path(kwargs["draft_dir"], "SKILL.md").write_text(
                self.content, encoding="utf-8"
            )
        context = get_hook_context()
        self.hook_context = context


class EvolverStub:
    def __init__(self, content=None):
        self.content = content
        self.calls = []

    async def evolve(self, **kwargs):
        self.calls.append(kwargs)
        if self.content is not None:
            Path(kwargs["draft_dir"], "SKILL.md").write_text(
                self.content, encoding="utf-8"
            )
        self.hook_context = get_hook_context()


def manager(tmp_path, producer, evolver=None, publisher=None, terminal_limit=100):
    job_manager = SkillJobManager(
        producer=producer,
        evolver=evolver or EvolverStub(),
        repository=SkillRepository(tmp_path / "global", tmp_path / "workspace"),
        coordinator=SkillWriteCoordinator(),
        workspace_dir=tmp_path,
        publish_event=publisher,
        terminal_limit=terminal_limit,
    )
    job_manager.bind_background_work_starter(
        lambda name, operation: asyncio.create_task(operation(), name=name)
    )
    return job_manager


async def wait_terminal(job_manager, job_id):
    for _ in range(100):
        job = job_manager.require(job_id)
        if job.is_terminal:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Job did not reach terminal state")


def test_submit_returns_queued_then_background_job_succeeds_and_notifies(tmp_path):
    async def run():
        events = []

        async def publish(event):
            events.append(event)

        job_manager = manager(
            tmp_path, ProducerStub(skill_content("new")), publisher=publish
        )
        await job_manager.start()
        queued = await asyncio.to_thread(
            job_manager.submit_produce,
            name="new",
            scope="workspace",
            instructions="create it",
            conversation=(Message("user", [TextPart("context")]),),
            task_id="task-1",
            run_id="run-1",
            step=3,
        )
        assert queued.status == "queued"
        completed = await wait_terminal(job_manager, queued.job_id)
        await asyncio.sleep(0)
        return job_manager, completed, events

    job_manager, completed, events = asyncio.run(run())

    assert completed.status == "succeeded"
    assert Path(completed.path).read_text(encoding="utf-8") == skill_content("new")
    assert len(events) == 1
    assert isinstance(events[0], TaskContextInputEvent)
    assert events[0].task_id == "task-1"
    assert completed.job_id in events[0].content
    stored = job_manager.require(completed.job_id)
    assert stored.notification_event_id == events[0].event_id
    assert job_manager._producer.hook_context.run_id is None
    assert job_manager._producer.hook_context.data["parent_run_id"] == "run-1"
    assert job_manager._producer.hook_context.data["skill_job_id"] == completed.job_id


def test_job_failure_is_safe_and_notification_failure_does_not_change_status(tmp_path):
    async def run():
        async def fail_publish(event):
            raise RuntimeError("transport secret")

        job_manager = manager(
            tmp_path,
            ProducerStub(error=RuntimeError("raw secret detail")),
            publisher=fail_publish,
        )
        await job_manager.start()
        queued = job_manager.submit_produce(
            name="new",
            scope="workspace",
            instructions="create",
            conversation=(),
            task_id="task",
            run_id="run",
            step=0,
        )
        completed = await wait_terminal(job_manager, queued.job_id)
        await asyncio.sleep(0)
        return job_manager.require(queued.job_id)

    job = asyncio.run(run())
    assert job.status == "failed"
    assert job.error == "Skill Job failed (RuntimeError)"
    assert "raw secret" not in job.error
    assert job.notification_event_id is not None
    assert job.notification_status is None


@pytest.mark.parametrize(
    "status",
    ["accepted", "not_found", "not_running", "already_cancelling", "already_finished", "invalid_content"],
)
def test_notification_result_is_correlated_for_all_existing_statuses(tmp_path, status):
    async def run():
        events = []

        async def publish(event):
            events.append(event)

        job_manager = manager(tmp_path, ProducerStub(skill_content("new")), publisher=publish)
        await job_manager.start()
        queued = job_manager.submit_produce(
            name="new", scope="workspace", instructions="x", conversation=(),
            task_id="task", run_id="run", step=0
        )
        await wait_terminal(job_manager, queued.job_id)
        for _ in range(100):
            if events:
                break
            await asyncio.sleep(0.01)
        assert events
        event = events[0]
        return job_manager.record_notification_result(
            TaskContextInputResultEvent(
                task_id="task", request_event_id=event.event_id, status=status
            )
        ), job_manager

    job, job_manager = asyncio.run(run())
    assert job.notification_status == status
    assert job_manager.record_notification_result(
        TaskContextInputResultEvent(
            task_id="task", request_event_id="missing", status=status
        )
    ) is None


def test_evolve_job_uses_snapshot_and_updates_workspace(tmp_path):
    source_dir = tmp_path / "workspace" / "existing"
    source_dir.mkdir(parents=True)
    source = source_dir / "SKILL.md"
    source.write_text(skill_content("existing", "old"), encoding="utf-8")

    async def run():
        evolver = EvolverStub(skill_content("existing", "new"))
        job_manager = manager(tmp_path, ProducerStub(), evolver=evolver)
        await job_manager.start()
        snapshot = job_manager._repository.capture("existing")
        assert snapshot is not None
        queued = job_manager.submit_evolve(
            name="existing", instructions="improve", conversation=(),
            task_id="task", run_id="run", step=1, snapshot=snapshot
        )
        return await wait_terminal(job_manager, queued.job_id), evolver

    job, evolver = asyncio.run(run())
    assert job.status == "succeeded"
    assert source.read_text(encoding="utf-8") == skill_content("existing", "new")
    assert evolver.calls[0]["snapshot"].directory_hash
    assert evolver.calls[0]["draft_dir"].parent.name == ".drafts"


def test_quiesce_rejects_new_job_and_drain_interrupts_generation(tmp_path):
    async def run():
        gate = asyncio.Event()
        job_manager = manager(tmp_path, ProducerStub(skill_content("new"), gate=gate))
        await job_manager.start()
        queued = job_manager.submit_produce(
            name="new", scope="workspace", instructions="x", conversation=(),
            task_id="task", run_id="run", step=0
        )
        await asyncio.sleep(0)
        await job_manager.quiesce()
        with pytest.raises(RuntimeError, match="quiescing"):
            job_manager.submit_produce(
                name="other", scope="workspace", instructions="x", conversation=(),
                task_id="task", run_id="run", step=0
            )
        await job_manager.drain()
        await job_manager.drain()
        await job_manager.stop()
        return job_manager.require(queued.job_id)

    job = asyncio.run(run())
    assert job.status == "interrupted"


def test_immediate_drain_does_not_leave_accepted_job_queued(tmp_path):
    async def run():
        job_manager = manager(tmp_path, ProducerStub(skill_content("new")))
        await job_manager.start()
        queued = job_manager.submit_produce(
            name="new", scope="workspace", instructions="x", conversation=(),
            task_id="task", run_id="run", step=0
        )
        await job_manager.quiesce()
        await job_manager.drain()
        return job_manager.require(queued.job_id)

    assert asyncio.run(run()).is_terminal


def test_drain_waits_for_prepare_then_cleans_orphan_draft(tmp_path):
    async def run():
        job_manager = manager(tmp_path, ProducerStub(skill_content("new")))
        await job_manager.start()
        entered = Event()
        release = Event()
        real_prepare = job_manager._repository.prepare_produce

        def blocking_prepare(*args):
            draft = real_prepare(*args)
            entered.set()
            release.wait(timeout=2)
            return draft

        job_manager._repository.prepare_produce = blocking_prepare
        queued = job_manager.submit_produce(
            name="new", scope="workspace", instructions="x", conversation=(),
            task_id="task", run_id="run", step=0
        )
        await asyncio.to_thread(entered.wait, 2)
        drain_task = asyncio.create_task(job_manager.drain())
        await asyncio.sleep(0.02)
        assert not drain_task.done()
        release.set()
        await drain_task
        return job_manager.require(queued.job_id)

    job = asyncio.run(run())

    assert job.status == "interrupted"
    assert list((tmp_path / "workspace" / ".drafts").iterdir()) == []


def test_drain_waits_for_commit_stage(tmp_path):
    async def run():
        job_manager = manager(tmp_path, ProducerStub(skill_content("new")))
        await job_manager.start()
        entered = Event()
        release = Event()
        real_produce = job_manager._repository.publish_produce

        def blocking_produce(*args):
            entered.set()
            release.wait(timeout=2)
            return real_produce(*args)

        job_manager._repository.publish_produce = blocking_produce
        queued = job_manager.submit_produce(
            name="new", scope="workspace", instructions="x", conversation=(),
            task_id="task", run_id="run", step=0
        )
        await asyncio.to_thread(entered.wait, 2)
        drain_task = asyncio.create_task(job_manager.drain())
        await asyncio.sleep(0.02)
        assert not drain_task.done()
        release.set()
        await drain_task
        return job_manager.require(queued.job_id)

    assert asyncio.run(run()).status == "succeeded"


def test_workspace_and_session_state_restore_interrupts_unfinished_and_prunes(tmp_path):
    async def run():
        job_manager = manager(tmp_path, ProducerStub(), terminal_limit=2)
        queued = SkillJob.create(
            job_id="queued", operation="produce", target_name="a", scope="workspace",
            task_id="task", run_id="run", step=0
        )
        running = SkillJob.create(
            job_id="running", operation="evolve", target_name="b", scope=None,
            task_id="task", run_id="run", step=1
        ).transition("running")
        succeeded = SkillJob.create(
            job_id="done", operation="produce", target_name="c", scope="global",
            task_id="task", run_id="run", step=2
        ).transition("running").transition("succeeded", path="/tmp/c")
        await job_manager.restore_workspace_state(
            {"jobs": [queued.to_dict(), running.to_dict(), succeeded.to_dict()]},
            state_version=1,
        )
        await job_manager.restore_session_state(
            {"job_ids": ["queued", "running", "done"], "notifications": {}},
            state_version=1,
        )
        return job_manager, await job_manager.snapshot_workspace_state(), await job_manager.snapshot_session_state()

    job_manager, workspace, session = asyncio.run(run())
    assert workspace is None
    assert len(session["job_ids"]) == 2
    assert len(session["jobs"]) == 2
    assert all(
        item["status"] in {"succeeded", "interrupted"}
        for item in session["jobs"]
    )
    with pytest.raises(KeyError, match="not found"):
        job_manager.require("unknown")


def test_session_state完整jobs可独立恢复无需workspace_state(tmp_path):
    async def run():
        original = manager(tmp_path, ProducerStub())
        finished = SkillJob.create(
            job_id="done", operation="produce", target_name="a",
            scope="workspace", task_id="task", run_id="run", step=0,
        ).transition("running").transition("succeeded", path="/tmp/a")
        await original.restore_session_state(
            {
                "job_ids": ["done"],
                "jobs": [finished.to_dict()],
                "notifications": {},
            },
            state_version=1,
        )
        snapshot = await original.snapshot_session_state()

        restored = manager(tmp_path, ProducerStub())
        await restored.restore_session_state(snapshot, state_version=1)
        return restored.require("done"), snapshot

    job, snapshot = asyncio.run(run())

    assert job.status == "succeeded"
    assert snapshot["jobs"][0]["job_id"] == "done"
