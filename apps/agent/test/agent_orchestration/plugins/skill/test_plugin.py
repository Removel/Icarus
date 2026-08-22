import asyncio
import json

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentErrorEvent,
    AgentResponse,
    AgentTextDeltaEvent,
    AgentToolCompletedEvent,
    AgentToolStartedEvent,
)
from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookDispatcher,
    HookRegistry,
)
from apps.agent.src.agent_orchestration.events import Event
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    RankedSkill,
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.skill.plugin import SkillPlugin
from apps.agent.src.agent_orchestration.plugins.skill.session_state import (
    SessionSkillState,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, TextPart, ToolCall


class ScannerStub:
    def __init__(self, skills):
        self.skills = skills
        self.calls = 0

    def scan(self):
        self.calls += 1
        return list(self.skills)


class UsageStoreStub:
    def __init__(self):
        self.ensure_calls = []
        self.mark_calls = []
        self.closed = False

    def ensure_discovered(self, workspace_key, skills):
        self.ensure_calls.append((workspace_key, list(skills)))
        return {}

    def mark_used(self, workspace_key, skills):
        self.mark_calls.append((workspace_key, list(skills)))
        return {}

    def close(self):
        self.closed = True


class EmbeddingStub:
    def __init__(self):
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


class RankerStub:
    def __init__(self):
        self.calls = []
        self.minimum_content_score = 0.8

    def rank_with_summary(self, skills, query_vector, document_vectors, usages):
        self.calls.append((skills, query_vector, document_vectors, usages))
        ranked = [
            RankedSkill(
                skill=skill,
                content_score=1.0,
                lifecycle_status="active",
                lifecycle_score=1.0,
                final_score=1.0,
            )
            for skill in skills[:3]
        ]
        return ranked, len(ranked)


class RecordingHook(BaseHook):
    def __init__(self):
        self.events = []

    def handle(self, event):
        self.events.append(event)


def make_skill(tmp_path, name):
    return SkillDefinition(
        name=name,
        description=f"description {name}",
        path=tmp_path / name / "SKILL.md",
        scope="global",
    )


def make_plugin(tmp_path, skills, **overrides):
    dependencies = {
        "scanner": ScannerStub(skills),
        "usage_store": UsageStoreStub(),
        "embedding": EmbeddingStub(),
        "ranker": RankerStub(),
        "session_state": SessionSkillState(),
    }
    dependencies.update(overrides)
    plugin = SkillPlugin(
        "skill",
        workspace_key="workspace-a",
        user_input_plugin_id="user-input",
        **dependencies,
    )
    published = []

    async def publish(event):
        published.append(event)

    plugin.bind_publisher(publish)
    return plugin, dependencies, published


def test_skill_plugin只处理指定来源的UserInputEvent(tmp_path):
    async def run():
        plugin, dependencies, published = make_plugin(
            tmp_path, [make_skill(tmp_path, "one")]
        )
        await plugin.consume("other", UserInputEvent(prompt="ignored"))
        await plugin.consume("user-input", Event())
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert dependencies["scanner"].calls == 0
    assert published == []


def test_skill_plugin只接受完整agent事件和失败input终态(tmp_path):
    plugin, _, _ = make_plugin(tmp_path, [])
    call = ToolCall(id="call-1", name="read", arguments={})
    completed = AgentCompletedEvent(
        step=1,
        response=AgentResponse(
            message=Message("assistant", [TextPart("done")]),
            finish_reason="stop",
            messages=[
                Message("user", [TextPart("hello")]),
                Message("assistant", [TextPart("done")]),
            ],
        ),
    )

    assert plugin.accepts_event("agent", completed) is True
    assert (
        plugin.accepts_event(
            "agent", AgentTextDeltaEvent(step=1, text="delta")
        )
        is False
    )
    assert (
        plugin.accepts_event(
            "agent", AgentToolStartedEvent(step=1, tool_call=call)
        )
        is False
    )
    assert (
        plugin.accepts_event(
            "agent",
            AgentToolCompletedEvent(
                step=1,
                tool_call=call,
                result=ToolExecutionResult(success=True),
            ),
        )
        is False
    )
    assert (
        plugin.accepts_event(
            "agent",
            AgentErrorEvent(
                step=1,
                error_type="RuntimeError",
                error_message="failed",
            ),
        )
        is False
    )
    assert (
        plugin.accepts_event(
            "user-input",
            InputFinishedEvent(
                task_id="task",
                status="failed",
            ),
        )
        is True
    )
    assert (
        plugin.accepts_event(
            "user-input",
            InputFinishedEvent(
                task_id="task",
                status="completed",
            ),
        )
        is False
    )


def test_skill_plugin空扫描直接发布completed且不调用embedding(tmp_path):
    async def run():
        plugin, dependencies, published = make_plugin(tmp_path, [])
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-empty", prompt="hello"),
        )
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert dependencies["embedding"].query_calls == []
    assert dependencies["embedding"].document_calls == []
    assert dependencies["usage_store"].ensure_calls == []
    assert len(published) == 1
    assert published[0].task_id == "task-empty"
    assert published[0].status == "completed"
    assert published[0].context_blocks == []


def test_skill_plugin已有累计skill后空扫描仍保留上下文并推进刷新计数(
    tmp_path,
):
    definition = make_skill(tmp_path, "one")

    async def run():
        scanner = ScannerStub([definition])
        plugin, _, published = make_plugin(
            tmp_path,
            [definition],
            scanner=scanner,
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="first"),
        )
        scanner.skills = []
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-2", prompt="second"),
        )
        return plugin, published

    plugin, published = asyncio.run(run())

    block = published[1].context_blocks[0]
    assert block.metadata == {"mode": "unchanged"}
    assert "unchanged" in block.content
    assert plugin.session_state.selected_skills == (definition,)
    assert plugin.session_state.unchanged_turns == 1


def test_skill_plugin正常检索并发布确定性full上下文(tmp_path):
    skills = [make_skill(tmp_path, name) for name in ["one", "two", "three", "four"]]

    async def run():
        plugin, dependencies, published = make_plugin(tmp_path, skills)
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="find skill"),
        )
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert dependencies["usage_store"].ensure_calls == [
        ("workspace-a", skills)
    ]
    assert dependencies["embedding"].query_calls == ["find skill"]
    assert dependencies["embedding"].document_calls == [
        [skill.description for skill in skills]
    ]
    assert dependencies["usage_store"].mark_calls == [
        ("workspace-a", skills[:3])
    ]
    event = published[0]
    assert event.status == "completed"
    block = event.context_blocks[0]
    assert block.source_plugin_id == "skill"
    assert block.context_type == "skills"
    assert block.metadata == {"mode": "full"}
    assert json.loads(block.content) == [
        {
            "description": skill.description,
            "name": skill.name,
            "path": str(skill.path),
        }
        for skill in skills[:3]
    ]
    assert block.content == json.dumps(
        json.loads(block.content),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_skill_plugin无新增发布unchanged但仍记录top3使用(tmp_path):
    skills = [make_skill(tmp_path, "one")]

    async def run():
        plugin, dependencies, published = make_plugin(tmp_path, skills)
        event = UserInputEvent(task_id="task-1", prompt="same")
        await plugin.consume("user-input", event)
        await plugin.consume("user-input", event)
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert len(dependencies["usage_store"].mark_calls) == 2
    block = published[1].context_blocks[0]
    assert block.metadata == {"mode": "unchanged"}
    assert block.content == (
        "The available skill context is unchanged from the previous full injection."
    )


def test_skill_plugin空召回不注入且不更新使用状态(tmp_path):
    class EmptyRanker(RankerStub):
        def rank_with_summary(
            self, skills, query_vector, document_vectors, usages
        ):
            return [], 0

    async def run():
        plugin, dependencies, published = make_plugin(
            tmp_path,
            [make_skill(tmp_path, "one")],
            ranker=EmptyRanker(),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-empty", prompt="hello"),
        )
        return plugin, dependencies, published

    plugin, dependencies, published = asyncio.run(run())

    assert dependencies["usage_store"].mark_calls == []
    assert plugin.session_state.selected_skills == ()
    assert published[0].status == "completed"
    assert published[0].context_blocks == []


def test_skill_plugin聚合检索hook不记录prompt向量或skill正文(tmp_path):
    registry = HookRegistry()
    hook = RecordingHook()
    registry.register("skill.retrieval", hook)

    async def run():
        plugin, _, _ = make_plugin(
            tmp_path,
            [make_skill(tmp_path, "one")],
            hook_dispatcher=HookDispatcher(registry),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(
                task_id="task-hook",
                prompt="SECRET PROMPT MUST NOT APPEAR",
            ),
        )

    asyncio.run(run())

    assert len(hook.events) == 1
    event = hook.events[0]
    assert event.name == "skill.retrieval"
    assert event.phase == "after"
    assert event.data["candidate_count"] == 1
    assert event.data["qualified_count"] == 1
    assert event.data["minimum_content_score"] == 0.8
    assert event.data["mode"] == "full"
    assert event.data["selected"][0]["name"] == "one"
    serialized = json.dumps(dict(event.data))
    assert "SECRET PROMPT MUST NOT APPEAR" not in serialized
    assert "description one" not in serialized


def test_skill_plugin同名workspace覆盖即使未进本轮top3也替换累计定义(
    tmp_path,
):
    global_skill = make_skill(tmp_path, "same")
    workspace_skill = SkillDefinition(
        name="same",
        description="workspace replacement",
        path=tmp_path / "workspace" / "same" / "SKILL.md",
        scope="workspace",
    )
    other = make_skill(tmp_path, "other")

    class SequencedRanker:
        def __init__(self):
            self.calls = 0
            self.minimum_content_score = 0.8

        def rank_with_summary(
            self, skills, query_vector, document_vectors, usages
        ):
            self.calls += 1
            selected = global_skill if self.calls == 1 else other
            return [
                RankedSkill(
                    skill=selected,
                    content_score=1.0,
                    lifecycle_status="active",
                    lifecycle_score=1.0,
                    final_score=1.0,
                )
            ], 1

    async def run():
        scanner = ScannerStub([global_skill])
        plugin, _, published = make_plugin(
            tmp_path,
            [global_skill],
            scanner=scanner,
            ranker=SequencedRanker(),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="first"),
        )
        scanner.skills = [other, workspace_skill]
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-2", prompt="second"),
        )
        return plugin, published

    plugin, published = asyncio.run(run())

    selected = plugin.session_state.selected_skills
    assert [skill.name for skill in selected] == ["same", "other"]
    assert selected[0] == workspace_skill
    assert published[1].context_blocks[0].metadata == {"mode": "full"}


def test_skill_plugin异常发布failed防止blackboard卡住(tmp_path):
    class FailingScanner:
        def scan(self):
            raise RuntimeError("scan failed")

    async def run():
        plugin, _, published = make_plugin(
            tmp_path, [], scanner=FailingScanner()
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-fail", prompt="hello"),
        )
        return published

    published = asyncio.run(run())
    assert len(published) == 1
    assert published[0].task_id == "task-fail"
    assert published[0].status == "failed"
    assert published[0].error == "scan failed"
    assert published[0].context_blocks == []


def test_skill_plugin聚合错误hook不透传可能包含prompt的底层异常(tmp_path):
    class LeakyScanner:
        def scan(self):
            raise RuntimeError("provider failed for SECRET USER PROMPT")

    registry = HookRegistry()
    hook = RecordingHook()
    registry.register("skill.retrieval", hook)

    async def run():
        plugin, _, _ = make_plugin(
            tmp_path,
            [],
            scanner=LeakyScanner(),
            hook_dispatcher=HookDispatcher(registry),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(
                task_id="task-error-hook",
                prompt="SECRET USER PROMPT",
            ),
        )

    asyncio.run(run())

    assert len(hook.events) == 1
    assert hook.events[0].phase == "error"
    assert hook.events[0].data["error_type"] == "RuntimeError"
    assert hook.events[0].data["error_message"] == "Skill retrieval failed"
    assert "SECRET USER PROMPT" not in json.dumps(dict(hook.events[0].data))


def test_skill_plugin检索超时后熔断并持续发布failed(tmp_path):
    class SlowEmbedding:
        def __init__(self):
            self.calls = 0

        async def embed_query(self, text):
            self.calls += 1
            await asyncio.Event().wait()

        async def embed_documents(self, texts):
            raise AssertionError("documents should not run")

    embedding = SlowEmbedding()

    async def run():
        plugin, _, published = make_plugin(
            tmp_path,
            [make_skill(tmp_path, "one")],
            embedding=embedding,
        )
        plugin.retrieval_timeout_seconds = 0.01
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="first"),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-2", prompt="second"),
        )
        return published

    published = asyncio.run(run())

    assert embedding.calls == 1
    assert [event.status for event in published] == ["failed", "failed"]
    assert all("timed out" in event.error for event in published)


def test_skill_plugin无usage_store仍正常检索发布(tmp_path):
    skills = [make_skill(tmp_path, "one")]

    async def run():
        plugin, dependencies, published = make_plugin(
            tmp_path, skills, usage_store=None
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="hello"),
        )
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert dependencies["ranker"].calls[0][3] == {}
    assert published[0].status == "completed"
    assert published[0].context_blocks[0].metadata == {"mode": "full"}


def test_skill_plugin_usage_store中途异常降级但仍发布completed(tmp_path):
    class FailingUsageStore:
        def ensure_discovered(self, workspace_key, skills):
            raise RuntimeError("ensure failed")

        def mark_used(self, workspace_key, skills):
            raise RuntimeError("mark failed")

        def close(self):
            pass

    skills = [make_skill(tmp_path, "one")]

    async def run():
        plugin, dependencies, published = make_plugin(
            tmp_path, skills, usage_store=FailingUsageStore()
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(task_id="task-1", prompt="hello"),
        )
        return dependencies, published

    dependencies, published = asyncio.run(run())
    assert dependencies["ranker"].calls[0][3] == {}
    assert published[0].status == "completed"
    assert len(published[0].context_blocks) == 1


def test_skill_plugin_stop关闭usage_store(tmp_path):
    async def run():
        plugin, dependencies, _ = make_plugin(tmp_path, [])
        await plugin.stop()
        return (
            dependencies["usage_store"].closed,
            dependencies["embedding"].closed,
        )

    assert asyncio.run(run()) == (True, True)


def test_skill_plugin_stop允许usage_store为空(tmp_path):
    async def run():
        plugin, _, _ = make_plugin(tmp_path, [], usage_store=None)
        await plugin.stop()
        return plugin.embedding.closed

    assert asyncio.run(run()) is True
