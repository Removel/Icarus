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
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    SkillWriteCoordinator,
)
from apps.agent.src.application.agent_runtime_service import AgentRuntimeService
from apps.agent.src.model_config import (
    ConfigModel,
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


class GenerationFactoryStub:
    def __init__(self) -> None:
        self.roles = []
        self.closed = False

    def get_agent(self, role):
        self.roles.append(role)
        raise AssertionError(
            "generation Agent must stay lazy without a write Job"
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


def test_runtime_service注册agent与skill工具并持有生成factory(tmp_path):
    async def run():
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=make_config(tmp_path / "data"),
        )
        assert not hasattr(service, "agent_factory")
        await service.start()
        agent_plugin = service.runtime_host.get_plugin("agent")
        skill_plugin = service.runtime_host.get_plugin("skill")
        agent_factory = agent_plugin.agent_factory
        close_resource = skill_plugin.job_manager._close_resource

        assert isinstance(agent_factory, AgentFactory)
        assert agent_factory.tool_registry is service.tool_registry
        assert isinstance(close_resource.__self__, AgentFactory)
        assert close_resource.__self__ is not agent_factory
        assert set(service.tool_registry.names()) >= {
            "skills_list",
            "skill_search",
            "skill_produce",
            "skill_evolve",
            "skill_job_status",
        }

        await service.stop(timeout=1)
        return skill_plugin

    skill_plugin = asyncio.run(run())

    assert skill_plugin.job_manager._stopped is True


def test_runtime_service_组装固定session并转发完整任务事件(tmp_path):
    async def run():
        generation_factory = GenerationFactoryStub()
        coordinator = SkillWriteCoordinator()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "generation_agent_factory": generation_factory,
            "skill_write_coordinator": coordinator,
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
            generation_factory,
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
        generation_factory,
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
    assert generation_factory.roles == []
    assert generation_factory.closed is True
    assert "skill" in agent_subscribers
    assert skill_runtime.processed_count == 0


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
        "skills_list",
        "skill_search",
        "skill_produce",
        "skill_evolve",
        "skill_job_status",
    }
    assert {owner for name, owner in graph.tools if name.startswith("skill")} == {"skill"}
    assert {owner for name, owner in graph.tools if not name.startswith("skill")} == {"builtin-tools"}
    assert ("skill", "agent") in graph.subscriptions
    assert graph.start_order[0] == "persistence"
    assert graph.stop_order[-1] == "persistence"
    blackboard = next(
        plugin for plugin in graph.plugins if plugin.plugin_id == "blackboard"
    )
    assert blackboard.state_scopes == ("session",)
    assert blackboard.session_state_version == 1
    skill = next(plugin for plugin in graph.plugins if plugin.plugin_id == "skill")
    assert skill.state_scopes == ("workspace", "session")
    assert ("user-input", "agent", "task_channels") in (
        graph.capability_bindings
    )
    assert ("skill", "blackboard", "conversation") in (
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
        generation_factory = GenerationFactoryStub()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "generation_agent_factory": generation_factory,
            "skill_write_coordinator": SkillWriteCoordinator(),
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
        return service, generation_factory

    service, generation_factory = asyncio.run(run())

    assert service.is_running is False
    assert service.session_id is None
    assert service.persistence.is_running is False
    assert generation_factory.closed is True


def test_runtime_service_stop被取消时等待plugin清理后再报告取消(tmp_path):
    async def run():
        generation_factory = GenerationFactoryStub()
        config = make_config(tmp_path / "data")
        config.runtime.plugin_config["skill"] = {
            "generation_agent_factory": generation_factory,
            "skill_write_coordinator": SkillWriteCoordinator(),
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


def test_runtime_service不自动注入skill并通过注册工具显式检索(tmp_path):
    async def run():
        data_dir = tmp_path / "data"
        skill_dir = data_dir / "skills" / "skill-product"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: skill-product\n"
            "description: Create reusable skills from completed work.\n"
            "keywords:\n"
            "  - reusable workflow\n"
            "---\n"
            "# Skill Product\n",
            encoding="utf-8",
        )
        config = make_config(data_dir)
        service = AgentRuntimeService(
            workspace_path=tmp_path,
            config=config,
        )
        agent = AgentStub()
        await service.start()
        agent_plugin = service.runtime_host.get_plugin("agent")
        agent_plugin.agent_factory.get_agent = lambda model_role: agent
        subscription = service.subscribe_events()
        accepted = await service.submit("create a reusable skill")
        await collect_task_events(subscription, accepted.task_id)
        skill_plugin = service.plugin_manager.registry.get("skill")
        blackboard = service.plugin_manager.registry.get("blackboard")
        search_tool = service.tool_registry.get("skill_search")
        assert search_tool is not None
        search_result = search_tool.invoke({"keywords": ["reusable"]})
        tool_names = {
            definition.name
            for definition in agent_plugin.agent_factory.tool_registry.definitions()
        }
        subscription.close()
        await service.stop(timeout=1)
        return (
            agent.calls,
            skill_plugin,
            blackboard,
            search_result,
            tool_names,
        )

    calls, skill_plugin, blackboard, search_result, tool_names = asyncio.run(
        run()
    )

    assert blackboard.required_context_sources == frozenset()
    assert calls[0]["tools"] is None
    assert "<plugin_context>" not in calls[0]["input_prompt"]
    assert calls[0]["input_prompt"] == (
        "<user_request>\ncreate a reusable skill\n</user_request>"
    )
    assert {
        "skills_list",
        "skill_search",
        "skill_produce",
        "skill_evolve",
        "skill_job_status",
    }.issubset(tool_names)
    assert search_result.success is True
    assert search_result.output == {
        "skills": [
            {
                "name": "skill-product",
                "description": (
                    "Create reusable skills from completed work."
                ),
                "path": str(skill_plugin.catalog.find_visible("skill-product").path),
                "scope": "global",
            }
        ]
    }
