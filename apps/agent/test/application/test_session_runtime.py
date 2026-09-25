import asyncio
import json

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentMessageCompletedEvent,
    AgentResponse,
    AgentTextDeltaEvent,
)
from apps.agent.src.agent_orchestration.plugins.persistence import (
    SessionIdentity,
)
from apps.agent.src.agent_orchestration.plugins.memory.models import (
    MemoryItem,
    MemoryRecallResult,
)
from apps.agent.src.application.session_runtime import (
    DEFAULT_SYSTEM_PROMPT,
    SessionRuntime,
)
from apps.agent.src.model_config import (
    ConfigModel,
    LLMConfig,
    ModelSettings,
    ThinkMode,
)
from apps.agent.src.model_provider.types import ImagePart, Message, TextPart, ToolCall, Usage


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
        runtime={
            "plugin_config": {
                "memory": {"user_id": "test-user", "agent_id": "test-agent"},
                "knowledge": {"knowledge_base": "test-kb"},
            }
        },
        model_settings=ModelSettings(thinking=model, perception=model),
    )


def test_default_system_prompt将记忆作为自身能力并允许自主维护():
    assert "你自己的长期记忆" in DEFAULT_SYSTEM_PROMPT
    assert "无需每次征求用户确认" in DEFAULT_SYSTEM_PROMPT
    assert "明确纠正" in DEFAULT_SYSTEM_PROMPT
    assert "以用户当前的明确说法为准" in DEFAULT_SYSTEM_PROMPT
    assert "不要记录密码、Token、Cookie、私钥" in DEFAULT_SYSTEM_PROMPT
    assert "删除或遗忘前先确认目标和范围" in DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt要求普通聊天简短自然且不暴露记忆机制():
    assert "不要说“根据记忆库”" in DEFAULT_SYSTEM_PROMPT
    assert "自然说“我记不起来了”" in DEFAULT_SYSTEM_PROMPT
    assert "不要说“没有召回到记忆”" in DEFAULT_SYSTEM_PROMPT
    assert "“没有关于你的记忆”" in DEFAULT_SYSTEM_PROMPT
    assert "“记忆库为空”" in DEFAULT_SYSTEM_PROMPT
    assert "不要据此断言长期记忆不存在" in DEFAULT_SYSTEM_PROMPT
    assert "普通聊天默认简短、自然、直接" in DEFAULT_SYSTEM_PROMPT
    assert "复杂任务" in DEFAULT_SYSTEM_PROMPT
    assert "按需要完整展开" in DEFAULT_SYSTEM_PROMPT


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


class MemoryBackendStub:
    def __init__(self, items=()):
        self.items = tuple(items)
        self.calls = []
        self.closed = False

    def recall(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return MemoryRecallResult(self.items, query)

    async def arecall(self, query, **kwargs):
        return self.recall(query, **kwargs)

    def close(self):
        self.closed = True

    async def aclose(self):
        pass


class MemoryAwareAgent:
    def __init__(self):
        self.context = None
        self.input_prompt = None

    async def astream(self, **kwargs):
        self.input_prompt = kwargs["input_prompt"]
        self.context = kwargs["run_control"].drain_context(
            applied_before_step=1
        )
        input_text = kwargs["input_prompt"]
        if self.context is not None:
            input_text = (
                f"{self.context.message.content[0].text}\n\n{input_text}"
            )
        message = Message("assistant", [TextPart("used memory")])
        yield AgentCompletedEvent(
            step=1,
            response=AgentResponse(
                message=message, usage=Usage(10, 2), last_usage=Usage(10, 2),
                finish_reason="stop", steps=1,
                messages=[Message("user", [TextPart(input_text)]), message],
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
            ) and not runtime.snapshot().has_work:
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
    assert "memory" in {item.plugin_id for item in graph.plugins}
    assert "knowledge" in {item.plugin_id for item in graph.plugins}
    assert "process" in {item.plugin_id for item in graph.plugins}
    assert "background_process" in runtime.tool_registry.names()
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
    assert "blackboard_list" in names
    assert "blackboard_read" in names
    assert {
        "knowledge_query", "knowledge_list", "knowledge_read",
        "knowledge_upload", "knowledge_recompile",
    }.issubset(names)


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


def test_session_runtime向当前task追加用户steer(tmp_path):
    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "session-steer"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        before_start = await runtime.steer_task("task-1", "before start")
        await runtime.start()
        agent = runtime.runtime_host.get_plugin("agent")
        channel = agent.task_channels.create("task-1")
        channel.mark_preparing_context()
        channel.start_run("run-1")

        image = ImagePart("assets/image.png", "asset", "image/png")
        accepted = await runtime.steer_task(
            "task-1", "only tests", [image],
            display_text="only [#image1]", submission_id="steer-1"
        )
        batch = channel.drain_context(applied_before_step=2)
        await runtime.stop("test", timeout=1)
        return before_start, accepted, batch

    before_start, accepted, batch = asyncio.run(run())

    assert before_start.status == "not_running"
    assert accepted.status == "accepted"
    assert accepted.run_id == "run-1"
    assert batch is not None
    assert batch.records[0].kind == "user_correction"
    assert batch.records[0].content == "only tests"
    assert batch.records[0].input_images == (
        ImagePart("assets/image.png", "asset", "image/png"),
    )
    assert batch.records[0].display_text == "only [#image1]"
    assert batch.records[0].event_id == "steer-1"


def test_session_runtime旧steer调用生成非空唯一event_id(tmp_path):
    async def run():
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path, "session-steer-legacy"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        agent = runtime.runtime_host.get_plugin("agent")
        channel = agent.task_channels.create("task-1")
        channel.mark_preparing_context()
        channel.start_run("run-1")
        await runtime.steer_task("task-1", "first")
        await runtime.steer_task("task-1", "second")
        batch = channel.drain_context(applied_before_step=2)
        await runtime.stop("test", timeout=1)
        return batch

    batch = asyncio.run(run())

    assert batch is not None
    event_ids = [record.event_id for record in batch.records]
    assert all(event_ids)
    assert len(set(event_ids)) == 2


def test_session_runtime_e2e持久化完整agent_run历史(tmp_path):
    class ToolTranscriptAgent:
        async def astream(self, **kwargs):
            prompt = kwargs["input_prompt"]
            tool_call = ToolCall("call-1", "read", {"path": "settings.json"})
            final = Message("assistant", [TextPart("final answer")])
            yield AgentCompletedEvent(
                step=2,
                response=AgentResponse(
                    message=final,
                    last_usage=Usage(999, 100),
                    messages=[
                        Message("user", [TextPart(prompt)]),
                        Message("assistant", [], tool_calls=[tool_call]),
                        Message("tool", [TextPart("tool result")], tool_call_id="call-1"),
                        Message("user", [TextPart("<runtime_context>secret context</runtime_context>")]),
                        final,
                    ],
                    task_message_start=0,
                    steps=2,
                ),
            )

    async def run():
        identity = SessionIdentity.create(tmp_path, "session-product")
        runtime = SessionRuntime(
            identity,
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = (
            lambda role: ToolTranscriptAgent()
        )
        accepted = await runtime.submit("original user text")
        for _ in range(200):
            if not runtime.snapshot().has_work:
                break
            await asyncio.sleep(0.01)
        await runtime.checkpoint()
        state_path = (
            runtime.persistence.resolver.session_dir(identity)
            / "plugin-state"
            / "blackboard.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        messages = runtime.runtime_host.get_plugin("blackboard").get_messages()
        tools = set(runtime.tool_registry.names())
        await runtime.stop("test", timeout=1)
        return accepted, messages, state, tools

    accepted, messages, state, tools = asyncio.run(run())
    assert accepted.task_id
    assert [message.role for message in messages] == [
        "user", "assistant", "tool", "user", "assistant"
    ]
    assert "original user text" in messages[0].content[0].text
    assert messages[1].tool_calls[0].id == "call-1"
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].content == [TextPart("tool result")]
    assert "secret context" in messages[3].content[0].text
    assert messages[-1] == Message("assistant", [TextPart("final answer")])
    stored = state["state"]["messages"]
    assert [item["role"] for item in stored] == [
        "user", "assistant", "tool", "user", "assistant"
    ]
    serialized = json.dumps(state, ensure_ascii=False)
    assert "tool result" in serialized
    assert "secret context" in serialized
    assert {"blackboard_list", "blackboard_read"}.issubset(tools)


def test_session_runtime_e2e自动记忆先注入再进入完整历史(tmp_path, monkeypatch):
    async def run():
        backend = MemoryBackendStub(
            [MemoryItem("memory:1", "user prefers concise docs", 0.95, "global")]
        )
        config = make_config(tmp_path / "data")
        monkeypatch.setattr(
            "apps.agent.src.agent_orchestration.plugins.memory.factory.Mem0HttpAdapter",
            lambda *args, **kwargs: backend,
        )
        identity = SessionIdentity.create(tmp_path, "session-memory")
        runtime = SessionRuntime(
            identity, config=config,
            publish_update=lambda update: asyncio.sleep(0),
        )
        await runtime.start()
        agent = MemoryAwareAgent()
        runtime.runtime_host.get_plugin("agent").agent_factory.get_agent = lambda role: agent
        await runtime.submit("write a design")
        for _ in range(200):
            if not runtime.snapshot().has_work:
                break
            await asyncio.sleep(0.01)
        blackboard = runtime.runtime_host.get_plugin("blackboard")
        region = blackboard.region_store.get("memory")
        messages = blackboard.get_messages()
        tools = set(runtime.tool_registry.names())
        await runtime.stop("test", timeout=1)
        return backend, agent, region, messages, tools

    backend, agent, region, messages, tools = asyncio.run(run())
    assert len(backend.calls) == 1
    assert agent.context is not None
    assert "user prefers concise docs" in agent.context.message.content[0].text
    assert '"memory":' in agent.input_prompt
    assert region.complete_for_input is True
    assert region.state.status == "idle"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert "user prefers concise docs" in messages[0].content[0].text
    assert "write a design" in messages[0].content[0].text
    assert messages[1] == Message("assistant", [TextPart("used memory")])
    assert {
        "memory_recall", "memory_get", "memory_history",
        "memory_remember", "memory_correct", "memory_stop_reference",
        "memory_restore_reference", "memory_delete",
    }.issubset(tools)


def test_session_runtime_e2e知识工具按需执行且不注册region(tmp_path, monkeypatch):
    from apps.agent.src.agent_orchestration.plugins.knowledge.models import (
        KnowledgeCatalog,
        KnowledgeDocument,
        KnowledgePage,
        KnowledgeQueryResult,
        KnowledgeRecompileDocument,
        KnowledgeRecompileResult,
        KnowledgeUploadItem,
        KnowledgeUploadResult,
    )

    class KnowledgeBackendStub:
        def __init__(self):
            self.calls = []

        def query(self, question):
            self.calls.append(("query", question))
            return KnowledgeQueryResult("Icarus uses plugin-owned capabilities.")

        def list(self):
            self.calls.append(("list",))
            return KnowledgeCatalog(
                (KnowledgeDocument("knowledge-document:h1", "guide.md", "md", "Markdown"),),
                ("summaries/guide",), (), (), (),
            )

        def read(self, path):
            self.calls.append(("read", path))
            return KnowledgePage(path, "Icarus architecture guide")

        def upload(self, sources):
            self.calls.append(("upload", tuple(source.name for source in sources)))
            return KnowledgeUploadResult(
                (KnowledgeUploadItem("guide.md", "added", "compiled"),), 1, 0, 0
            )

        def recompile(self, *, document, all_documents, refresh_schema):
            self.calls.append(("recompile", document, all_documents, refresh_schema))
            return KnowledgeRecompileResult(
                "done", 1, 1, 0,
                (KnowledgeRecompileDocument("guide.md", "guide", "md", "recompiled"),),
            )

        def close(self):
            pass

    async def run():
        backend = KnowledgeBackendStub()
        monkeypatch.setattr(
            "apps.agent.src.agent_orchestration.plugins.knowledge.factory.OpenKBHttpAdapter",
            lambda *args, **kwargs: backend,
        )
        runtime = SessionRuntime(
            SessionIdentity.create(tmp_path / "workspace", "session-knowledge"),
            config=make_config(tmp_path / "data"),
            publish_update=lambda update: asyncio.sleep(0),
        )
        runtime.workspace_path.mkdir(parents=True)
        (runtime.workspace_path / "guide.md").write_text("guide", encoding="utf-8")
        await runtime.start()
        tools = {name: runtime.tool_registry.get(name) for name in runtime.tool_registry.names()}
        outputs = [
            tools["knowledge_upload"].invoke({"paths": ["guide.md"]}),
            tools["knowledge_list"].invoke({}),
            tools["knowledge_read"].invoke({"path": "summaries/guide"}),
            tools["knowledge_query"].invoke({"question": "What is Icarus?"}),
            tools["knowledge_recompile"].invoke({"document": "guide"}),
        ]
        region_names = {
            item.region
            for item in runtime.runtime_host.get_plugin("blackboard").region_store.snapshots()
        }
        await runtime.stop("test", timeout=1)
        return backend.calls, outputs, region_names

    calls, outputs, region_names = asyncio.run(run())
    assert all(output.success for output in outputs)
    assert [call[0] for call in calls] == [
        "upload", "list", "read", "query", "recompile"
    ]
    assert "knowledge" not in region_names
