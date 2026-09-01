import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading

import pytest

from apps.agent.src.agent_orchestration.plugins.persistence import (
    DataPathResolver,
    SessionIdentity,
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
    SessionNotFoundError,
    SubmissionConflictError,
)
from apps.agent.src.application.resource_ref import ResourceRef
from apps.agent.src.application.runtime_status import (
    SessionRuntimeSnapshot,
    TaskStatus,
)
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import Message, TextPart, Usage
from apps.agent.src.runtime_update import RuntimeUpdate


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

    async def submit(
        self,
        prompt,
        input_images=None,
        *,
        task_id=None,
    ):
        del prompt, input_images
        self.submit_count += 1
        return InputAccepted(task_id or f"task-{self.submit_count}", 0)

    async def checkpoint(self):
        return ()

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
    assert resumed.task_id
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
    assert accepted.task_id
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
    assert accepted.task_id
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
        "user.message",
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


def test_agent_runtime持久化公共历史并在终态checkpoint(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        session_id = await runtime.create_session(tmp_path, "history")
        entry = next(iter(runtime._entries.values()))
        entry.runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda role: AgentStub()
        )
        accepted = await runtime.submit(
            tmp_path,
            session_id,
            "model prompt",
            display_text="visible prompt",
            submission_id="history-submit",
        )
        for _ in range(100):
            records, cursor = await runtime.get_session_history(
                tmp_path, session_id
            )
            if any(item.type == "task.finished" for item in records):
                break
            await asyncio.sleep(0.01)
        blackboard_path = (
            DataPathResolver(config.icarus_data_dir)
            .session_dir(entry.identity)
            / "plugin-state"
            / "blackboard.json"
        )
        await runtime.stop()
        return accepted, records, cursor, blackboard_path.read_text()

    accepted, records, cursor, blackboard = asyncio.run(run())
    assert [item.type for item in records] == [
        "user.message",
        "task.accepted",
        "task.started",
        "assistant.text_delta",
        "task.usage",
        "task.finished",
    ]
    assert records[0].task_id == accepted.task_id
    assert records[0].payload["text"] == "visible prompt"
    assert [item.sequence for item in records] == list(range(1, 7))
    assert cursor == 6
    assert "model prompt" in blackboard


def test_agent_runtime历史查询收束异常退出task且不加载session(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        runtime = AgentRuntime(config_loader=lambda: config)
        await runtime.start()
        identity = SessionIdentity.create(tmp_path, "interrupted")
        DataPathResolver(config.icarus_data_dir).ensure_session(identity)
        runtime._history_store().append(
            identity,
            RuntimeUpdate(
                workspace_key=identity.workspace_key,
                session_id=identity.session_id,
                task_id="task",
                type="assistant.text_delta",
                payload={"step": 1, "text": "partial"},
                occurred_at=datetime.now(UTC),
            ),
        )
        records, cursor = await runtime.get_session_history(
            tmp_path, identity.session_id
        )
        entry = runtime._entries[(identity.workspace_key, identity.session_id)]
        await runtime.stop()
        return records, cursor, entry.runtime

    records, cursor, loaded = asyncio.run(run())
    assert [item.type for item in records] == [
        "assistant.text_delta",
        "task.finished",
    ]
    assert records[-1].payload["status"] == "interrupted"
    assert records[-1].payload["recovered"] is True
    assert cursor == 2
    assert loaded is None


def test_agent_runtime已加载session不把排队task误判为interrupted(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        session_id = await runtime.create_session(tmp_path, "loaded")
        entry = next(iter(runtime._entries.values()))
        runtime._history_store().append(
            entry.identity,
            RuntimeUpdate(
                workspace_key=entry.identity.workspace_key,
                session_id=session_id,
                task_id="queued",
                type="user.message",
                payload={"text": "queued", "resources": []},
                occurred_at=datetime.now(UTC),
            ),
        )
        runtime._remember_task(
            entry,
            TaskStatus(
                workspace_key=entry.identity.workspace_key,
                session_id=session_id,
                task_id="queued",
                lifecycle="accepted",
            ),
        )
        records, cursor = await runtime.get_session_history(
            tmp_path, session_id
        )
        await runtime.stop()
        return records, cursor

    records, cursor = asyncio.run(run())
    assert [item.type for item in records] == ["user.message"]
    assert cursor == 1


def test_agent_runtime列出非空session摘要且不加载runtime(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        resolver = DataPathResolver(config.icarus_data_dir)
        empty = SessionIdentity.create(tmp_path, "empty")
        older = SessionIdentity.create(tmp_path, "older")
        newer = SessionIdentity.create(tmp_path, "newer")
        for identity in (empty, older, newer):
            resolver.ensure_session(identity)
        store = runtime._history_store()
        old_time = datetime(2026, 1, 1, tzinfo=UTC)
        new_time = old_time + timedelta(minutes=1)
        store.append(
            older,
            RuntimeUpdate(
                workspace_key=older.workspace_key,
                session_id=older.session_id,
                task_id="old",
                type="user.message",
                payload={"text": "  old\n session ", "resources": []},
                occurred_at=old_time,
            ),
        )
        store.append(
            newer,
            RuntimeUpdate(
                workspace_key=newer.workspace_key,
                session_id=newer.session_id,
                task_id="new",
                type="user.message",
                payload={"text": "new session", "resources": []},
                occurred_at=new_time,
            ),
        )
        summaries = runtime.list_session_summaries(tmp_path)
        entries = dict(runtime._entries)
        await runtime.stop()
        return summaries, entries, factory

    summaries, entries, factory = asyncio.run(run())
    assert [(item.session_id, item.first_user_input) for item in summaries] == [
        ("newer", "new session"),
        ("older", "old session"),
    ]
    assert entries == {}
    assert factory.created == []


def test_agent_runtime只丢弃空session(tmp_path):
    async def run():
        config = make_config(tmp_path / "data")
        factory = RuntimeFactory()
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=factory
        )
        await runtime.start()
        loaded = await runtime.create_session(tmp_path, "loaded")
        busy = await runtime.create_session(tmp_path, "busy")
        non_empty = await runtime.create_session(tmp_path, "non-empty")
        factory.created[1].busy = True
        await runtime.submit(
            tmp_path, non_empty, "hello", submission_id="message"
        )
        loaded_result = await runtime.discard_empty_session(tmp_path, loaded)
        busy_result = await runtime.discard_empty_session(tmp_path, busy)
        non_empty_result = await runtime.discard_empty_session(
            tmp_path, non_empty
        )
        missing_result = await runtime.discard_empty_session(
            tmp_path, "missing"
        )
        resolver = DataPathResolver(config.icarus_data_dir)
        exists = {
            session_id: resolver.session_exists(
                SessionIdentity.create(tmp_path, session_id)
            )
            for session_id in (loaded, busy, non_empty)
        }
        factory.created[1].busy = False
        await runtime.stop()
        return (
            loaded_result,
            busy_result,
            non_empty_result,
            missing_result,
            exists,
            factory.created[0].stop_reasons,
        )

    loaded, busy, non_empty, missing, exists, stop_reasons = asyncio.run(run())
    assert loaded.status == "discarded"
    assert busy.status == "busy"
    assert non_empty.status == "not_empty"
    assert missing.status == "not_found"
    assert exists == {"loaded": False, "busy": True, "non-empty": True}
    assert stop_reasons == ["discard_empty"]


def test_agent_runtime丢弃期间并发submit不会复活旧entry(tmp_path, monkeypatch):
    async def run():
        config = make_config(tmp_path / "data")
        runtime = AgentRuntime(
            config_loader=lambda: config, session_factory=RuntimeFactory()
        )
        await runtime.start()
        identity = SessionIdentity.create(tmp_path, "session")
        DataPathResolver(config.icarus_data_dir).ensure_session(identity)
        started = threading.Event()
        release = threading.Event()
        original = DataPathResolver.discard_session

        def blocking_discard(resolver, target):
            started.set()
            assert release.wait(timeout=2)
            return original(resolver, target)

        monkeypatch.setattr(DataPathResolver, "discard_session", blocking_discard)
        discard = asyncio.create_task(
            runtime.discard_empty_session(tmp_path, identity.session_id)
        )
        assert await asyncio.to_thread(started.wait, 2)
        submit = asyncio.create_task(
            runtime.submit(
                tmp_path, identity.session_id, "late", submission_id="late"
            )
        )
        await asyncio.sleep(0)
        release.set()
        result = await discard
        with pytest.raises(SessionNotFoundError):
            await submit
        entry_exists = (identity.workspace_key, identity.session_id) in runtime._entries
        await runtime.stop()
        return result, entry_exists

    result, entry_exists = asyncio.run(run())
    assert result.status == "discarded"
    assert entry_exists is False
