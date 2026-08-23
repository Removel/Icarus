import asyncio
import json

import pytest

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.agent_factory import AgentFactory
from apps.agent.src.agent_orchestration.run_control import (
    TaskCancelResultEvent,
    TaskContextInputEvent,
    TaskContextInputResultEvent,
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


class BlockingAgentStub:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def astream(self, **kwargs):
        del kwargs
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if False:
            yield


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
    subscription,
    task_id: str,
):
    events = []
    while True:
        source_plugin_id, event = await asyncio.wait_for(
            subscription.next_event(),
            timeout=1,
        )
        if event.task_id != task_id:
            continue
        events.append((source_plugin_id, event))
        if isinstance(event, InputFinishedEvent):
            return events


def test_runtime_service由所属plugin创建并持有agent_factory(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        assert not hasattr(service, "agent_factory")
        assert not hasattr(service, "maintenance_agent_factory")

        await service.start()
        agent_plugin = service.runtime_host.get_plugin("agent")
        skill_plugin = service.runtime_host.get_plugin("skill")
        agent_factory = agent_plugin.agent_factory
        maintenance_factory = skill_plugin.maintenance_agent_factory

        assert isinstance(agent_factory, AgentFactory)
        assert agent_factory.tool_registry is service.tool_registry
        assert isinstance(maintenance_factory, AgentFactory)
        assert maintenance_factory is not agent_factory

        await service.stop(timeout=1)
        return skill_plugin

    skill_plugin = asyncio.run(run())

    assert skill_plugin.maintenance_agent_factory is None


def test_runtime_service_组装固定session并转发完整任务事件(tmp_path):
    async def run():
        maintenance_factory = MaintenanceFactoryStub()
        coordinator = WorkspaceMaintenanceCoordinator()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "maintenance_agent_factory": maintenance_factory,
            "maintenance_coordinator": coordinator,
        }
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="session-1",
            config=config,
        )
        agent = AgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        first_subscription = service.subscribe_events()
        second_subscription = service.subscribe_events()
        accepted = await service.submit("hello")
        events, second_events = await asyncio.gather(
            collect_task_events(first_subscription, accepted.task_id),
            collect_task_events(second_subscription, accepted.task_id),
        )
        running_session_id = service.session_id
        first_subscription.close()
        second_subscription.close()
        await service.stop(timeout=1)
        return (
            service,
            accepted,
            events,
            second_events,
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
        second_events,
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
    assert second_events == events
    assert service.is_running is False
    assert service.session_id is None
    assert skipped_count == 0
    assert maintenance_factory.roles == []
    assert maintenance_factory.closed is True
    assert coordinator.active_workspace_keys == frozenset()
    assert "skill" in agent_subscribers
    assert skill_runtime.processed_count == 2


def test_runtime_service由manifest生成冻结运行图并保存退出快照(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="graph-session",
            config=make_config(tmp_path / "data"),
        )
        await service.start()
        graph = service.runtime_host.graph_snapshot
        diagnostics = tuple(service.runtime_host.diagnostics.items)
        await service.stop(timeout=1)
        snapshot_path = (
            tmp_path
            / "data"
            / "workspaces"
            / service.persistence.workspace_identity.workspace_key
            / "sessions"
            / "graph-session"
            / "runtime-snapshot.json"
        )
        return graph, diagnostics, snapshot_path

    graph, diagnostics, snapshot_path = asyncio.run(run())

    assert graph is not None
    assert {plugin.plugin_id for plugin in graph.plugins} == {
        "persistence",
        "builtin-tools",
        "agent",
        "user-input",
        "skill",
        "blackboard",
        "output-bridge",
    }
    assert {name for name, owner in graph.tools} == {
        "read",
        "write",
        "insert",
        "bash",
    }
    assert all(owner == "builtin-tools" for _, owner in graph.tools)
    assert ("skill", "agent") in graph.subscriptions
    assert graph.start_order[0] == "persistence"
    assert graph.stop_order[-1] == "persistence"
    blackboard = next(
        plugin for plugin in graph.plugins if plugin.plugin_id == "blackboard"
    )
    assert blackboard.state_scopes == ("session",)
    assert blackboard.session_state_version == 1
    assert ("user-input", "agent", "task_channels") in (
        graph.capability_bindings
    )
    assert graph.diagnostics == ()
    assert not [item for item in diagnostics if item.level == "error"]
    assert snapshot_path.is_file()


def test_runtime_service_未启动时拒绝提交和订阅事件(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        with pytest.raises(RuntimeError, match="not running"):
            await service.submit("hello")
        with pytest.raises(RuntimeError, match="not running"):
            service.subscribe_events()

    asyncio.run(run())


def test_runtime_service_未启动时取消返回not_running(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        cancel = await service.cancel_task("task-1", "stop")
        return cancel

    cancel = asyncio.run(run())

    assert cancel.status == "not_running"


def test_runtime_service准备阶段取消task(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        agent = AgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()

        accepted = await service.submit("hello")
        cancel = await service.cancel_task(accepted.task_id, "user_requested")
        events = await collect_task_events(subscription, accepted.task_id)
        subscription.close()
        await service.stop(timeout=1)
        return accepted, cancel, events, agent.calls

    accepted, cancel, events, calls = asyncio.run(run())

    assert cancel.status == "accepted"
    assert calls == []
    finished = next(
        event for _, event in events if isinstance(event, InputFinishedEvent)
    )
    assert finished.status == "cancelled"
    assert not any(
        isinstance(event, (TaskContextInputResultEvent, TaskCancelResultEvent))
        for _, event in events
    )


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


def test_runtime_service停止时复用task取消收束活动agent(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        agent = BlockingAgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        await service.submit("keep running")
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        assert service.runtime_host.status == "running"

        await service.stop(timeout=1)
        return service, agent

    service, agent = asyncio.run(run())

    assert agent.cancelled is True
    assert service.is_running is False
    assert service.persistence.is_running is False


def test_runtime_service由manifest接通skill运行中介入事件(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        agent = BlockingAgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()
        accepted = await service.submit("keep running")
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        request = TaskContextInputEvent(
            task_id=accepted.task_id,
            content="skill job completed",
        )
        await service.runtime_host.get_plugin("skill").publish(request)
        while True:
            source, event = await asyncio.wait_for(
                subscription.next_event(), timeout=1
            )
            if isinstance(event, TaskContextInputResultEvent):
                result = (source, event)
                break
        subscription.close()
        await service.stop(timeout=1)
        return request, result

    request, (source, result) = asyncio.run(run())

    assert source == "agent"
    assert result.request_event_id == request.event_id
    assert result.status == "accepted"


def test_runtime_service_停止时关闭仍存活的输出订阅(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        await service.start()
        subscription = service.subscribe_events()
        waiter = asyncio.create_task(subscription.next_event())
        await asyncio.sleep(0)

        await service.stop(timeout=1)

        with pytest.raises(RuntimeError, match="subscription is closed"):
            await asyncio.wait_for(waiter, timeout=1)
        return subscription.closed

    assert asyncio.run(run()) is True


def test_runtime_service_start被取消时完成全部资源清理(tmp_path):
    async def run():
        maintenance_factory = MaintenanceFactoryStub()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "maintenance_agent_factory": maintenance_factory,
            "maintenance_coordinator": WorkspaceMaintenanceCoordinator(),
        }
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=config,
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
        "apps.agent.src.agent_orchestration.plugins.skill.factory.SkillUsageStore",
        blocking_store,
    )

    async def run():
        embedding = EmbeddingStub()
        maintenance_factory = MaintenanceFactoryStub()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "embedding": embedding,
            "maintenance_agent_factory": maintenance_factory,
            "maintenance_coordinator": WorkspaceMaintenanceCoordinator(),
        }
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=config,
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
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "maintenance_agent_factory": MaintenanceFactoryStub(),
            "maintenance_coordinator": WorkspaceMaintenanceCoordinator(),
        }
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=config,
        )
        await service.start()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_stop = service.plugin_manager.stop

        async def blocking_stop(timeout=None, **kwargs):
            entered.set()
            await release.wait()
            await original_stop(timeout=timeout, **kwargs)

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

        agent_factory = service.runtime_host.get_plugin(
            "agent"
        ).agent_factory
        agent_factory.aclose = fail_to_close
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
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()

        first = await service.submit("first")
        await collect_task_events(subscription, first.task_id)
        second = await service.submit("second")
        await collect_task_events(subscription, second.task_id)
        subscription.close()
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


def test_runtime_service重启后从session快照恢复blackboard历史(tmp_path):
    async def run_first(config):
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="restored-session",
            config=config,
        )
        agent = AgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()
        accepted = await service.submit("first")
        await collect_task_events(subscription, accepted.task_id)
        subscription.close()
        await service.stop(timeout=1)

    async def run_second(config):
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="restored-session",
            config=config,
        )
        agent = AgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()
        accepted = await service.submit("second")
        await collect_task_events(subscription, accepted.task_id)
        subscription.close()
        await service.stop(timeout=1)
        return agent.calls[0]["history_messages"]

    config = make_config(tmp_path / "data")
    asyncio.run(run_first(config))
    restored = asyncio.run(run_second(config))

    assert restored == [
        Message("user", [TextPart("<user_request>\nfirst\n</user_request>")]),
        Message("assistant", [TextPart("answer:<user_request>\nfirst\n</user_request>")]),
    ]


def test_runtime_service核心plugin状态版本不兼容时拒绝ready(tmp_path):
    async def prepare(config):
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="invalid-state-session",
            config=config,
        )
        await service.start()
        await service.stop(timeout=1)
        return service.persistence.workspace_identity.workspace_key

    config = make_config(tmp_path / "data")
    workspace_key = asyncio.run(prepare(config))
    state_path = (
        tmp_path
        / "data"
        / "workspaces"
        / workspace_key
        / "sessions"
        / "invalid-state-session"
        / "plugin-state"
        / "blackboard.json"
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["state_version"] = 999
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    async def restart():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            session_id="invalid-state-session",
            config=config,
        )
        with pytest.raises(RuntimeError, match="Invalid session state"):
            await service.start()
        return service

    service = asyncio.run(restart())
    assert service.is_running is False
    assert service.persistence.is_running is False


def test_runtime_service配置指定的核心plugin缺失时拒绝ready(tmp_path):
    config = make_config(tmp_path / "data")
    config.runtime.required_plugin_ids.append("required-external")

    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path, config=config
        )
        with pytest.raises(RuntimeError, match="required-external"):
            await service.start()
        return service

    service = asyncio.run(run())
    assert service.is_running is False
    assert service.persistence.is_running is False


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
        config = make_config(data_dir)
        config.runtime.plugin_config["skill"] = {"embedding": embedding}
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=config,
        )
        agent = AgentStub()
        await service.start()
        service.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda model_role: agent
        )
        subscription = service.subscribe_events()
        accepted = await service.submit("create a reusable skill")
        await collect_task_events(subscription, accepted.task_id)
        skill_plugin = service.plugin_manager.registry.get("skill")
        blackboard = service.plugin_manager.registry.get("blackboard")
        subscription.close()
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
