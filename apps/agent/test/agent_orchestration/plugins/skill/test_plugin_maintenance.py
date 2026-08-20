import asyncio
from dataclasses import dataclass
import json

from apps.agent.src.agent_orchestration.capability import (
    AgentCompletedEvent,
    AgentResponse,
)
from apps.agent.src.agent_orchestration.plugins.skill.coordinator import (
    WorkspaceMaintenanceCoordinator,
)
from apps.agent.src.agent_orchestration.plugins.skill.models import (
    SkillDefinition,
)
from apps.agent.src.agent_orchestration.plugins.skill.plugin import SkillPlugin
from apps.agent.src.agent_orchestration.plugins.skill.session_state import (
    SessionSkillState,
)
from apps.agent.src.agent_orchestration.plugins.skill.turn_state import (
    SkillTurnState,
)
from apps.agent.src.agent_orchestration.plugins.user_input.events import (
    InputFinishedEvent,
    UserInputEvent,
)
from apps.agent.src.agent_orchestration.tools import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, TextPart, ToolCall


class ScannerStub:
    def __init__(self, skills=()):
        self.skills = list(skills)

    def scan(self):
        return list(self.skills)


class UsageStoreStub:
    def __init__(self):
        self.marked = []
        self.removed = []
        self.closed = False

    def ensure_discovered(self, workspace_key, skills):
        return {}

    def mark_used(self, workspace_key, skills):
        self.marked.append((workspace_key, tuple(skills)))
        return {}

    def remove(self, workspace_key, skill_keys):
        self.removed.append((workspace_key, tuple(skill_keys)))
        return len(tuple(skill_keys))

    def activate_after_maintenance(self, workspace_key, skills):
        self.marked.append((workspace_key, tuple(skills)))
        return {}

    def close(self):
        self.closed = True


class EmbeddingStub:
    async def embed_query(self, text):
        return [1.0]

    async def embed_documents(self, texts):
        return [[1.0] for _ in texts]

    async def aclose(self):
        pass


class RankerStub:
    minimum_content_score = 0.8

    def rank_with_summary(self, skills, query_vector, document_vectors, usages):
        return [], 0


class MaintainerStub:
    def __init__(self, plan=None, gate=None):
        self.plan_value = plan or Plan(())
        self.gate = gate
        self.calls = []
        self.started = asyncio.Event()

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.plan_value


@dataclass(frozen=True)
class Plan:
    operations: tuple


@dataclass(frozen=True)
class ResultItem:
    action: str = "no_op"
    target_name: str = ""
    status: str = "skipped"
    target_written: bool = False
    file_deleted: bool = False
    deleted_sources: tuple[str, ...] = ()
    cleanup_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchResult:
    results: tuple[ResultItem, ...]


class RepositoryStub:
    def __init__(self, snapshots=(), result=None):
        self.snapshots = tuple(snapshots)
        self.result = result or BatchResult((ResultItem(),))
        self.apply_calls = []

    def snapshot(self, **kwargs):
        return self.snapshots

    def apply(self, operations, snapshots):
        self.apply_calls.append((tuple(operations), tuple(snapshots)))
        return self.result


def make_skill(tmp_path, name="maintained"):
    return SkillDefinition(
        name=name,
        description=name,
        path=tmp_path / name / "SKILL.md",
        scope="workspace",
    )


def make_plugin(tmp_path, *, maintainer=None, repository=None, coordinator=None):
    scanner = ScannerStub()
    usage = UsageStoreStub()
    plugin = SkillPlugin(
        "skill",
        workspace_key="workspace-a",
        user_input_plugin_id="user-input",
        agent_plugin_id="agent",
        scanner=scanner,
        usage_store=usage,
        embedding=EmbeddingStub(),
        ranker=RankerStub(),
        session_state=SessionSkillState(),
        maintainer=maintainer,
        repository=repository,
        coordinator=coordinator,
        turn_state=SkillTurnState(),
    )
    published = []

    async def publish(event):
        published.append(event)

    plugin.bind_publisher(publish)
    return plugin, scanner, usage, published


def tool_messages(count):
    messages = [Message("user", [TextPart("full turn")])]
    for index in range(count):
        call = ToolCall(
            id=f"call-{index}",
            name="read",
            arguments={"index": index},
        )
        messages.append(
            Message(
                "assistant",
                [],
                tool_calls=[call],
            )
        )
        result = ToolExecutionResult(
            success=index % 2 == 0,
            error=("failed" if index % 2 else None),
        )
        messages.append(
            Message(
                "tool",
                [TextPart(json.dumps(result.as_dict()))],
                tool_call_id=call.id,
            )
        )
    messages.append(Message("assistant", [TextPart("done")]))
    return messages


def completed_event(correlation_id, *, finish_reason="stop", tool_count=0):
    messages = tool_messages(tool_count)
    return AgentCompletedEvent(
        correlation_id=correlation_id,
        step=12,
        response=AgentResponse(
            message=messages[-1],
            finish_reason=finish_reason,
            steps=12,
            messages=messages,
        ),
    )


async def feed_turn(plugin, correlation_id, tool_count):
    await plugin.consume(
        "user-input",
        UserInputEvent(correlation_id=correlation_id, prompt="work"),
    )
    await plugin.consume(
        "agent",
        completed_event(correlation_id, tool_count=tool_count),
    )


def test_plugin十次不触发十一次触发且失败工具也计数(tmp_path):
    async def run():
        maintainer = MaintainerStub()
        repository = RepositoryStub()
        coordinator = WorkspaceMaintenanceCoordinator()
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=maintainer,
            repository=repository,
            coordinator=coordinator,
        )
        await feed_turn(plugin, "ten", 10)
        await plugin.drain()
        assert maintainer.calls == []

        await feed_turn(plugin, "eleven", 11)
        await plugin.drain()
        return maintainer, repository, coordinator

    maintainer, repository, coordinator = asyncio.run(run())

    assert len(maintainer.calls) == 1
    traces = maintainer.calls[0]["tool_trace"]
    assert len(traces) == 11
    assert sum(trace.result.success is False for trace in traces) == 5
    assert repository.apply_calls == [((), ())]
    assert coordinator.active_workspace_keys == frozenset()


def test_plugin失败对话不触发且清理轮状态(tmp_path):
    async def run():
        maintainer = MaintainerStub()
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=maintainer,
            repository=RepositoryStub(),
            coordinator=WorkspaceMaintenanceCoordinator(),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(correlation_id="failed", prompt="work"),
        )
        await plugin.consume(
            "user-input",
            InputFinishedEvent(
                correlation_id="failed",
                task_id="failed",
                status="failed",
            ),
        )
        await plugin.consume(
            "agent", completed_event("failed", tool_count=11)
        )
        await plugin.drain()
        return maintainer

    assert asyncio.run(run()).calls == []


def test_plugin非stop终止原因不触发维护(tmp_path):
    async def run():
        maintainer = MaintainerStub()
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=maintainer,
            repository=RepositoryStub(),
            coordinator=WorkspaceMaintenanceCoordinator(),
        )
        await plugin.consume(
            "user-input",
            UserInputEvent(correlation_id="truncated", prompt="work"),
        )
        await plugin.consume(
            "agent",
            completed_event(
                "truncated",
                finish_reason="length",
                tool_count=11,
            ),
        )
        await plugin.drain()
        return maintainer

    assert asyncio.run(run()).calls == []


def test_plugin后台维护不阻塞完成事件且drain等待(tmp_path):
    async def run():
        gate = asyncio.Event()
        maintainer = MaintainerStub(gate=gate)
        coordinator = WorkspaceMaintenanceCoordinator()
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=maintainer,
            repository=RepositoryStub(),
            coordinator=coordinator,
        )
        await feed_turn(plugin, "background", 11)
        assert len(maintainer.calls) == 0
        await asyncio.wait_for(maintainer.started.wait(), timeout=1)
        assert len(maintainer.calls) == 1
        assert coordinator.is_claimed("workspace-a") is True

        drain = asyncio.create_task(plugin.drain())
        await asyncio.sleep(0)
        assert drain.done() is False
        gate.set()
        await drain
        return coordinator

    coordinator = asyncio.run(run())
    assert coordinator.is_claimed("workspace-a") is False


def test_plugin同workspace已有维护时新触发直接跳过(tmp_path):
    async def run():
        maintainer = MaintainerStub()
        coordinator = WorkspaceMaintenanceCoordinator()
        token = coordinator.claim("workspace-a")
        assert isinstance(token, str)
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=maintainer,
            repository=RepositoryStub(),
            coordinator=coordinator,
        )
        await feed_turn(plugin, "busy", 11)
        await plugin.drain()
        coordinator.release("workspace-a", token)
        return maintainer

    assert asyncio.run(run()).calls == []


def test_plugin按真实副作用激活写入目标并删除已删usage(tmp_path):
    maintained = make_skill(tmp_path, "maintained")
    deleted = make_skill(tmp_path, "old")
    result = BatchResult(
        (
            ResultItem(
                action="merge",
                target_name="maintained",
                status="failed",
                target_written=True,
                deleted_sources=("old",),
                cleanup_errors=("partial cleanup",),
            ),
        )
    )

    async def run():
        plugin, scanner, usage, _ = make_plugin(
            tmp_path,
            maintainer=MaintainerStub(),
            repository=RepositoryStub(result=result),
            coordinator=WorkspaceMaintenanceCoordinator(),
        )
        scanner.skills = [maintained, deleted]
        await feed_turn(plugin, "partial", 11)
        scanner.skills = [maintained]
        await plugin.drain()
        return usage

    usage = asyncio.run(run())

    assert any(
        skills == (maintained,) for _, skills in usage.marked
    )
    assert ("workspace-a", ("workspace:old",)) in usage.removed


def test_plugin_stop取消后台并释放workspace_claim(tmp_path):
    async def run():
        gate = asyncio.Event()
        coordinator = WorkspaceMaintenanceCoordinator()
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=MaintainerStub(gate=gate),
            repository=RepositoryStub(),
            coordinator=coordinator,
        )
        await feed_turn(plugin, "cancel", 11)
        await asyncio.sleep(0)
        assert coordinator.is_claimed("workspace-a") is True
        await plugin.stop()
        await asyncio.sleep(0)
        return coordinator, plugin

    coordinator, plugin = asyncio.run(run())
    assert coordinator.is_claimed("workspace-a") is False
    assert plugin._maintenance_tasks == set()


def test_plugin取消维护后repository线程完成前不释放claim(tmp_path):
    class BlockingRepository(RepositoryStub):
        def __init__(self, started, release):
            super().__init__()
            self.started = started
            self.release = release

        def apply(self, operations, snapshots):
            self.started.set()
            self.release.wait()
            return self.result

    async def run():
        import threading

        started = threading.Event()
        release = threading.Event()
        coordinator = WorkspaceMaintenanceCoordinator()
        repository = BlockingRepository(started, release)
        plugin, _, _, _ = make_plugin(
            tmp_path,
            maintainer=MaintainerStub(),
            repository=repository,
            coordinator=coordinator,
        )
        await feed_turn(plugin, "blocking-write", 11)
        await asyncio.to_thread(started.wait, 1)
        assert coordinator.is_claimed("workspace-a") is True

        maintenance_task = next(iter(plugin._maintenance_tasks))
        maintenance_task.cancel()
        await asyncio.gather(maintenance_task, return_exceptions=True)
        assert coordinator.is_claimed("workspace-a") is True

        release.set()
        await asyncio.gather(
            *tuple(plugin._repository_tasks),
            return_exceptions=True,
        )
        await asyncio.sleep(0)
        return coordinator

    coordinator = asyncio.run(run())
    assert coordinator.is_claimed("workspace-a") is False
