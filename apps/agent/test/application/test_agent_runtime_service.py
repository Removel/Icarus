import asyncio

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.plugins import (
    InputFinishedEvent,
    InputQueuedEvent,
    InputStartedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.plugins.skill import (
    WorkspaceMaintenanceCoordinator,
)
from apps.agent.src.application.agent_runtime_service import AgentRuntimeService
from apps.agent.src.model_config import (
    ConfigModel,
    EmbeddingSettings,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import Message, TextPart


def make_config(data_dir) -> ConfigModel:
    model = LLMConfig(
        model_name="test-model",
        max_tokens=1024,
        temperature=0,
        default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        icarus_data_dir=data_dir,
        embedding=EmbeddingSettings(
            provider="fastembed",
            model_name=(
                "sentence-transformers/"
                "paraphrase-multilingual-MiniLM-L12-v2"
            ),
        ),
        model_settings=ModelSettings(
            thinking=model,
            perception=model,
        ),
    )


class AgentStub:
    def __init__(self) -> None:
        self.calls = []

    async def astream(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["input_prompt"]
        message = Message("assistant", [TextPart(f"answer:{prompt}")])
        yield AgentTextDeltaEvent(step=1, text=f"answer:{prompt}")
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message,
                finish_reason="stop",
                steps=1,
                messages=[
                    Message("user", [TextPart(prompt)]),
                    message,
                ],
            ),
        )


class EmbeddingStub:
    def __init__(self) -> None:
        self.query_calls = []
        self.document_calls = []
        self.closed = False

    async def embed_query(self, text):
        self.query_calls.append(text)
        return [1.0, 0.0]

    async def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    async def aclose(self):
        self.closed = True


class MaintenanceFactoryStub:
    def __init__(self) -> None:
        self.roles = []
        self.closed = False

    def get_agent(self, role):
        self.roles.append(role)
        raise AssertionError(
            "maintenance Agent must stay lazy below the tool threshold"
        )

    async def aclose(self):
        self.closed = True


async def collect_task_events(
    service: AgentRuntimeService,
    task_id: str,
):
    events = []
    while True:
        source_plugin_id, event = await asyncio.wait_for(
            service.next_event(),
            timeout=1,
        )
        if event.correlation_id != task_id:
            continue
        events.append((source_plugin_id, event))
        if isinstance(event, InputFinishedEvent):
            return events


def test_runtime_service_组装固定session并转发完整任务事件(tmp_path):
    async def run():
        maintenance_factory = MaintenanceFactoryStub()
        coordinator = WorkspaceMaintenanceCoordinator()
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="session-1",
            config=make_config(tmp_path / "data"),
            maintenance_agent_factory=maintenance_factory,
            maintenance_coordinator=coordinator,
        )
        agent = AgentStub()
        service.agent_factory.get_agent = lambda model_role: agent
        await service.start()
        accepted = await service.submit("hello")
        events = await collect_task_events(service, accepted.task_id)
        running_session_id = service.session_id
        await service.stop(timeout=1)
        return (
            service,
            accepted,
            events,
            running_session_id,
            service.persistence.trace_hook.skipped_count,
            maintenance_factory,
            coordinator,
            service.plugin_manager.registry.get_subscriber_ids("agent"),
            service.plugin_manager.get_runtime_snapshot("skill"),
        )

    (
        service,
        accepted,
        events,
        running_session_id,
        skipped_count,
        maintenance_factory,
        coordinator,
        agent_subscribers,
        skill_runtime,
    ) = asyncio.run(run())

    assert accepted.queue_position == 0
    assert running_session_id == "session-1"
    assert [source for source, _ in events] == [
        "user-input",
        "user-input",
        "user-input",
        "agent",
        "agent",
        "user-input",
    ]
    assert isinstance(events[0][1], InputQueuedEvent)
    assert isinstance(events[1][1], InputStartedEvent)
    assert isinstance(events[2][1], UserInputEvent)
    assert isinstance(events[3][1], AgentTextDeltaEvent)
    assert isinstance(events[4][1], AgentCompletedEvent)
    assert isinstance(events[5][1], InputFinishedEvent)
    assert events[5][1].status == "completed"
    assert service.is_running is False
    assert service.session_id is None
    assert skipped_count == 0
    assert maintenance_factory.roles == []
    assert maintenance_factory.closed is True
    assert coordinator.active_workspace_keys == frozenset()
    assert "skill" in agent_subscribers
    assert skill_runtime.processed_count == 2


def test_runtime_service_未启动时拒绝提交和读取事件(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        with pytest.raises(RuntimeError, match="not running"):
            await service.submit("hello")
        with pytest.raises(RuntimeError, match="not running"):
            await service.next_event()

    asyncio.run(run())


def test_runtime_service_停止后再次启动给出明确生命周期错误(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        await service.start()
        await service.stop(timeout=1)

        with pytest.raises(RuntimeError, match="cannot be restarted"):
            await service.start()

    asyncio.run(run())


def test_runtime_service_start被取消时完成全部资源清理(tmp_path):
    async def run():
        maintenance_factory = MaintenanceFactoryStub()
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
            maintenance_agent_factory=maintenance_factory,
            maintenance_coordinator=WorkspaceMaintenanceCoordinator(),
        )
        start_entered = asyncio.Event()

        async def block_start():
            start_entered.set()
            await asyncio.Event().wait()

        service.plugin_manager.start = block_start
        task = asyncio.create_task(service.start())
        await start_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return service, maintenance_factory

    service, maintenance_factory = asyncio.run(run())

    assert service.is_running is False
    assert service.session_id is None
    assert service.persistence.is_running is False
    assert maintenance_factory.closed is True


def test_runtime_service_usage_store初始化线程中取消仍关闭临时资源(
    tmp_path,
    monkeypatch,
):
    import threading

    started = threading.Event()
    release = threading.Event()
    stores = []

    class StoreStub:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def blocking_store(path):
        started.set()
        release.wait()
        store = StoreStub()
        stores.append(store)
        return store

    monkeypatch.setattr(
        "apps.agent.src.application.agent_runtime_service.SkillUsageStore",
        blocking_store,
    )

    async def run():
        embedding = EmbeddingStub()
        maintenance_factory = MaintenanceFactoryStub()
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
            embedding=embedding,
            maintenance_agent_factory=maintenance_factory,
            maintenance_coordinator=WorkspaceMaintenanceCoordinator(),
        )
        task = asyncio.create_task(service.start())
        assert await asyncio.to_thread(started.wait, 1) is True
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return service, embedding, maintenance_factory

    service, embedding, maintenance_factory = asyncio.run(run())

    assert stores and stores[0].closed is True
    assert embedding.closed is True
    assert maintenance_factory.closed is True
    assert service.persistence.is_running is False
    assert service.session_id is None


def test_runtime_service_stop被取消时等待plugin清理后再报告取消(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
            maintenance_agent_factory=MaintenanceFactoryStub(),
            maintenance_coordinator=WorkspaceMaintenanceCoordinator(),
        )
        await service.start()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_stop = service.plugin_manager.stop

        async def blocking_stop(timeout=None):
            entered.set()
            await release.wait()
            await original_stop(timeout=timeout)

        service.plugin_manager.stop = blocking_stop
        stop_task = asyncio.create_task(service.stop(timeout=1))
        await entered.wait()
        stop_task.cancel()
        await asyncio.sleep(0)
        assert service.is_running is True
        assert service.persistence.is_running is True

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stop_task
        return service

    service = asyncio.run(run())

    assert service.is_running is False
    assert service.persistence.is_running is False
    assert service.session_id is None


def test_runtime_service_llm关闭失败时仍清理session和持久化(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        await service.start()

        async def fail_to_close():
            raise RuntimeError("close failed")

        service.agent_factory.aclose = fail_to_close
        with pytest.raises(RuntimeError, match="close failed"):
            await service.stop(timeout=1)
        return service

    service = asyncio.run(run())

    assert service.is_running is False
    assert service.session_id is None
    assert service.persistence.is_running is False


def test_runtime_service_由blackboard维护跨轮history并支持初始化消息(tmp_path):
    async def run():
        restored = [
            Message("user", [TextPart("restored-user")]),
            Message("assistant", [TextPart("restored-assistant")]),
        ]
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
            initial_messages=restored,
        )
        agent = AgentStub()
        service.agent_factory.get_agent = lambda model_role: agent
        await service.start()

        first = await service.submit("first")
        await collect_task_events(service, first.task_id)
        second = await service.submit("second")
        await collect_task_events(service, second.task_id)
        await service.stop(timeout=1)
        return agent.calls

    calls = asyncio.run(run())

    assert calls[0]["history_messages"] == [
        Message("user", [TextPart("restored-user")]),
        Message("assistant", [TextPart("restored-assistant")]),
    ]
    assert calls[1]["history_messages"] == [
        Message("user", [TextPart("restored-user")]),
        Message("assistant", [TextPart("restored-assistant")]),
        Message(
            "user",
            [TextPart("<user_request>\nfirst\n</user_request>")],
        ),
        Message(
            "assistant",
            [TextPart("answer:<user_request>\nfirst\n</user_request>")],
        ),
    ]


def test_runtime_service_检索skill并注入blackboard_prompt(tmp_path):
    async def run():
        data_dir = tmp_path / "data"
        skill_dir = data_dir / "skills" / "skill-product"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: skill-product\n"
            "description: Create reusable skills from completed work.\n"
            "---\n"
            "# Skill Product\n",
            encoding="utf-8",
        )
        embedding = EmbeddingStub()
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(data_dir),
            embedding=embedding,
        )
        agent = AgentStub()
        service.agent_factory.get_agent = lambda model_role: agent
        await service.start()
        accepted = await service.submit("create a reusable skill")
        await collect_task_events(service, accepted.task_id)
        skill_plugin = service.plugin_manager.registry.get("skill")
        blackboard = service.plugin_manager.registry.get("blackboard")
        await service.stop(timeout=1)
        return agent.calls, embedding, skill_plugin, blackboard

    calls, embedding, skill_plugin, blackboard = asyncio.run(run())

    assert embedding.query_calls == ["create a reusable skill"]
    assert embedding.document_calls == [
        ["Create reusable skills from completed work."]
    ]
    assert skill_plugin.session_state.selected_skills[0].name == "skill-product"
    assert blackboard.required_context_sources == frozenset({"skill"})
    assert "<plugin_context>" in calls[0]["input_prompt"]
    assert "skill-product" in calls[0]["input_prompt"]
    assert "<user_request>\ncreate a reusable skill\n</user_request>" in (
        calls[0]["input_prompt"]
    )
