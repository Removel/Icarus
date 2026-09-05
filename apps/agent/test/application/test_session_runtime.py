import asyncio

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    SessionIdentity,
)
from apps.agent.src.application.session_runtime import SessionRuntime
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import Message, TextPart, Usage


def make_config(data_dir) -> ConfigModel:
    model = LLMConfig(
        model_name="test-model",
        context_window=128000,
        max_tokens=1024,
        temperature=0,
        default_think_level=ThinkMode.LOW,
    )
    return ConfigModel(
        openai_base_url="https://openai.example.com/v1",
        anthropic_base_url="https://anthropic.example.com",
        icarus_data_dir=data_dir,
        model_settings=ModelSettings(thinking=model, perception=model),
    )


class AgentStub:
    async def astream(self, **kwargs):
        prompt = kwargs["input_prompt"]
        message = Message("assistant", [TextPart(f"answer:{prompt}")])
        yield AgentTextDeltaEvent(step=1, text="answer")
        yield AgentMessageCompletedEvent(step=1, message=message)
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message,
                usage=Usage(10, 2),
                last_usage=Usage(10, 2),
                finish_reason="stop",
                steps=1,
                messages=[Message("user", [TextPart(prompt)]), message],
                task_message_start=0,
            ),
        )


def test_session_runtime使用runtime_update并保留单session行为(tmp_path):
    async def run():
        updates = []

        async def publish(update):
            updates.append(update)

        identity = SessionIdentity.create(tmp_path, "session-1")
        runtime = SessionRuntime(
            identity,
            config=make_config(tmp_path / "data"),
            publish_update=publish,
        )
        await runtime.start()
        runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda role: AgentStub()
        )
        accepted = await runtime.submit("hello")
        for _ in range(100):
            if any(
                update.type == "task.finished"
                and update.task_id == accepted.task_id
                for update in updates
            ):
                break
            await asyncio.sleep(0.01)
        before_stop = runtime.snapshot()
        graph = runtime.runtime_host.graph_snapshot
        await runtime.stop("test", timeout=1)
        return runtime, updates, before_stop, graph

    runtime, updates, snapshot, graph = asyncio.run(run())

    assert [update.type for update in updates] == [
        "task.accepted",
        "task.started",
        "assistant.text_delta",
        "assistant.message",
        "task.usage",
        "task.finished",
    ]
    assert all(update.session_id == "session-1" for update in updates)
    assert snapshot.has_work is False
    assert graph is not None
    assert "runtime-update" in {item.plugin_id for item in graph.plugins}
    assert "mcp" in {item.plugin_id for item in graph.plugins}
    assert runtime.is_running is False


def test_session_runtime将mcp作为标准内置plugin并透传配置(tmp_path):
    config = make_config(tmp_path / "data")
    config.mcp_servers = {
        "blender": {"url": "http://127.0.0.1:9876/mcp"}
    }
    identity = SessionIdentity.create(tmp_path, "session-1")
    runtime = SessionRuntime(
        identity,
        config=config,
        publish_update=lambda update: asyncio.sleep(0),
    )

    assert "mcp" in runtime.runtime_host.required_plugin_ids
    assert runtime.runtime_host.graph.plugin_configs["mcp"]["servers"] == (
        config.mcp_servers
    )


def test_session_runtime无mcp_server时仍注册稳定入口(tmp_path):
    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "session-empty-mcp"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        names = runtime.tool_registry.names()
        await runtime.stop("test", timeout=1)
        return names

    names = asyncio.run(run())
    assert "mcp_tool_list" in names
    assert "mcp_tool_search" in names
    assert "mcp_tool_execute" in names


def test_session_runtime配置mcp后注册三个固定工具(tmp_path):
    config = make_config(tmp_path / "data")
    config.mcp_servers = {
        "blender": {"url": "http://127.0.0.1:9876/mcp"}
    }

    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "session-mcp"),
            config=config,
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        plugin = runtime.runtime_host.get_plugin("mcp")
        names = runtime.tool_registry.names()
        bridge_started = plugin.bridge.is_running
        await runtime.stop("test", timeout=1)
        return names, bridge_started

    names, bridge_started = asyncio.run(run())
    assert "mcp_tool_list" in names
    assert "mcp_tool_search" in names
    assert "mcp_tool_execute" in names
    assert bridge_started is False


def test_session_runtime_stop可重复调用(tmp_path):
    async def run():
        identity = SessionIdentity.create(tmp_path, "session-1")
        runtime = SessionRuntime(
            identity,
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        await runtime.stop("first", timeout=1)
        await runtime.stop("second", timeout=1)
        return runtime

    assert asyncio.run(run()).is_running is False
