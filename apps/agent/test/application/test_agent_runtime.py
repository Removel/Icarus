import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
)
from apps.agent.src.agent_orchestration.plugins.user_input import InputAccepted
from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.run_control import TaskOperationResult
from apps.agent.src.application.agent_runtime import (
    AgentRuntime,
    SessionAlreadyExistsError,
    SubmissionConflictError,
)
from apps.agent.src.application.resource_ref import ResourceRef
from apps.agent.src.application.runtime_status import SessionRuntimeSnapshot
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import Message, TextPart, Usage


def make_config(data_dir):
    model = LLMConfig(
        model_name="test", context_window=1000, max_tokens=100,
        temperature=0, default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com",
        anthropic_base_url="https://anthropic.example.com",
        icarus_data_dir=data_dir,
        model_settings=ModelSettings(thinking=model, perception=model),
    )


@dataclass
class RuntimeFactory:
    gate: asyncio.Event | None = None
    error: BaseException | None = None
    created: list = field(default_factory=list)

    def __call__(self, identity, *, config, publish_update, logger):
        runtime = SessionStub(
            identity, config, publish_update, self.gate, self.error
        )
        self.created.append(runtime)
        return runtime


class SessionStub:
    def __init__(self, identity, config, publish_update, gate=None, error=None):
        self.identity = identity
        self.config = config
        self.publish_update = publish_update
        self.gate = gate
        self.error = error
        self.is_running = False
        self.stop_reasons = []
        self.submit_count = 0
        self.busy = False
        self.imported = []

    async def start(self):
        if self.gate is not None:
            await self.gate.wait()
        DataPathResolver(self.config.icarus_data_dir).ensure_session(self.identity)
        if self.error is not None:
            raise self.error
        self.is_running = True

    async def submit(self, prompt, input_images=None):
        del prompt, input_images
        self.submit_count += 1
        return InputAccepted(f"task-{self.submit_count}", 0)

    async def cancel_task(self, task_id, reason=None):
        del reason
        return TaskOperationResult(task_id=task_id, status="accepted")

    def import_resources(self, paths):
        self.imported.extend(paths)
        return []

    def snapshot(self):
        return SessionRuntimeSnapshot(
            active_task_ids=(("task",) if self.busy else ()),
            queued_task_count=0,
            pending_event_count=0,
            pending_plugin_event_count=0,
            background_work_count=0,
            last_event_at=None,
            last_background_work_at=None,
        )

    async def stop(self, reason, timeout=30):
        del timeout
        self.stop_reasons.append(reason)
        self.is_running = False


class AgentStub:
    async def astream(self, **kwargs):
        prompt = kwargs["input_prompt"]
        message = Message("assistant", [TextPart("done")])
        yield AgentTextDeltaEvent(step=1, text="done")
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message,
                usage=Usage(4, 1),
                last_usage=Usage(4, 1),
                finish_reason="stop",
                steps=1,
                messages=[Message("user", [TextPart(prompt)]), message],
                task_message_start=0,
            ),
        )


def test_agent_runtime_create_submit幂等与unload_resume(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        session_id = await runtime.create_session(tmp_path, "session")
        first = await runtime.submit(
            tmp_path, session_id, "hello", submission_id="submit-1"
        )
        duplicate = await runtime.submit(
            tmp_path, session_id, "hello", submission_id="submit-1"
        )
        with pytest.raises(SubmissionConflictError):
            await runtime.submit(
                tmp_path, session_id, "different", submission_id="submit-1"
            )
        unloaded = await runtime.unload_session(tmp_path, session_id)
        resumed = await runtime.submit(
            tmp_path, session_id, "again", submission_id="submit-2"
        )
        await runtime.stop()
        return first, duplicate, resumed, unloaded, factory

    first, duplicate, resumed, unloaded, factory = asyncio.run(run())
    assert duplicate == first
    assert resumed.task_id == "task-1"
    assert unloaded.status == "unloaded"
    assert len(factory.created) == 2


def test_agent_runtime并发resume严格single_flight且查询不等待写锁(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        identity_dir = DataPathResolver(config.icarus_data_dir)
        from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
        identity_dir.ensure_session(SessionIdentity.create(tmp_path, "session"))
        gate = asyncio.Event()
        factory = RuntimeFactory(gate=gate)
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        first = asyncio.create_task(
            runtime.submit(tmp_path, "session", "a", submission_id="a")
        )
        second = asyncio.create_task(
            runtime.submit(tmp_path, "session", "b", submission_id="b")
        )
        await asyncio.sleep(0)
        status = runtime.get_session_status(tmp_path, "session")
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        gate.set()
        accepted = await second
        await runtime.stop()
        return status, accepted, factory

    status, accepted, factory = asyncio.run(run())
    assert status.lifecycle == "loading"
    assert accepted.task_id == "task-1"
    assert len(factory.created) == 1


def test_agent_runtime并发resume等待者共享同一次失败(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        from apps.agent.src.agent_orchestration.plugins.persistence import SessionIdentity
        DataPathResolver(config.icarus_data_dir).ensure_session(
            SessionIdentity.create(tmp_path, "session")
        )
        gate = asyncio.Event()
        error = RuntimeError("shared failure")
        factory = RuntimeFactory(gate=gate, error=error)
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        first = asyncio.create_task(
            runtime.submit(tmp_path, "session", "a", submission_id="a")
        )
        second = asyncio.create_task(
            runtime.submit(tmp_path, "session", "b", submission_id="b")
        )
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        await runtime.stop()
        return results, error, factory

    results, error, factory = asyncio.run(run())
    assert results == [error, error]
    assert len(factory.created) == 1


def test_agent_runtime_create失败保留目录且后续可重试(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        failing = RuntimeFactory(error=RuntimeError("boom"))
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=failing
        )
        await runtime.start()
        with pytest.raises(RuntimeError, match="boom"):
            await runtime.create_session(tmp_path, "session")
        status = runtime.get_session_status(tmp_path, "session")
        successful = RuntimeFactory()
        runtime._session_factory = successful
        accepted = await runtime.submit(
            tmp_path, "session", "retry", submission_id="retry"
        )
        with pytest.raises(SessionAlreadyExistsError):
            await runtime.create_session(tmp_path, "session")
        await runtime.stop()
        return status, accepted, successful

    status, accepted, successful = asyncio.run(run())
    assert status.lifecycle == "failed"
    assert accepted.task_id == "task-1"
    assert len(successful.created) == 1


def test_agent_runtime空闲扫描与busy复检(tmp_path):
    async def run():
        now = datetime(2026, 1, 1, tzinfo=UTC)
        config = make_config(tmp_path / "data")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config,
            session_factory=factory,
            clock=lambda: now,
            idle_timeout=timedelta(hours=6),
            cleanup_interval=timedelta(hours=2),
        )
        await runtime.start()
        await runtime.create_session(tmp_path, "session")
        now = now + timedelta(hours=7)
        factory.created[0].busy = True
        await runtime.cleanup_idle_sessions()
        busy = runtime.get_session_status(tmp_path, "session")
        factory.created[0].busy = False
        await runtime.cleanup_idle_sessions()
        unloaded = runtime.get_session_status(tmp_path, "session")
        await runtime.stop()
        return busy, unloaded, factory.created[0].stop_reasons

    busy, unloaded, reasons = asyncio.run(run())
    assert busy.lifecycle == "running"
    assert unloaded.lifecycle == "unloaded"
    assert reasons == ["idle_timeout"]


def test_agent_runtime资源在接受前导入且幂等重试不再读取暂存文件(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        incoming = config.icarus_data_dir / "incoming"
        incoming.mkdir(parents=True)
        source = incoming / "image.png"
        source.write_bytes(b"image")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        await runtime.create_session(tmp_path, "session")
        resource = ResourceRef("image.png")
        first = await runtime.submit(
            tmp_path, "session", "image", submission_id="id",
            resources=(resource,),
        )
        source.unlink()
        second = await runtime.submit(
            tmp_path, "session", "image", submission_id="id",
            resources=(resource,),
        )
        await runtime.stop()
        return first, second, factory.created[0].imported

    first, second, imported = asyncio.run(run())
    assert second == first
    assert len(imported) == 1


def test_agent_runtime真实session提交不重入锁且终态回到ready(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        subscription = runtime.subscribe_updates()
        session_id = await runtime.create_session(tmp_path, "real")
        entry = next(iter(runtime._entries.values()))
        entry.runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda role: AgentStub()
        )
        accepted = await asyncio.wait_for(
            runtime.submit(
                tmp_path, session_id, "hello", submission_id="submit"
            ),
            timeout=1,
        )
        updates = []
        for _ in range(20):
            update = await asyncio.wait_for(
                subscription.next_update(), timeout=1
            )
            updates.append(update)
            if (
                update.type == "session.lifecycle"
                and update.payload["status"] == "ready"
                and any(item.type == "task.finished" for item in updates)
            ):
                break
        for _ in range(100):
            status = runtime.get_session_status(tmp_path, session_id)
            if status.lifecycle == "ready":
                break
            await asyncio.sleep(0.01)
        task = runtime.get_task_status(
            tmp_path, session_id, accepted.task_id
        )
        subscription.close()
        await runtime.stop()
        return updates, status, task

    updates, status, task = asyncio.run(run())
    assert [item.type for item in updates] == [
        "session.lifecycle",
        "session.lifecycle",
        "task.accepted",
        "session.lifecycle",
        "task.started",
        "assistant.text_delta",
        "task.usage",
        "task.finished",
        "session.lifecycle",
    ]
    assert status.lifecycle == "ready"
    assert task.lifecycle == "completed"


def test_agent_runtime真实session在接受task前导入resource(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        incoming = config.icarus_data_dir / "incoming" / "client"
        incoming.mkdir(parents=True)
        source = incoming / "image.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        session_id = await runtime.create_session(tmp_path, "resource")
        entry = next(iter(runtime._entries.values()))
        entry.runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda role: AgentStub()
        )
        accepted = await runtime.submit(
            tmp_path,
            session_id,
            "describe",
            submission_id="resource-submit",
            resources=(ResourceRef("client/image.png", "image/png"),),
        )
        assets = tuple(
            entry.runtime.persistence.resolver.assets_dir(
                entry.identity
            ).iterdir()
        )
        source.unlink()
        duplicate = await runtime.submit(
            tmp_path,
            session_id,
            "describe",
            submission_id="resource-submit",
            resources=(ResourceRef("client/image.png", "image/png"),),
        )
        await runtime.stop()
        return accepted, duplicate, assets

    accepted, duplicate, assets = asyncio.run(run())
    assert duplicate == accepted
    assert len(assets) == 1
    assert assets[0].read_bytes().startswith(b"\x89PNG")
