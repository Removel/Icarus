import asyncio
import time

import pytest

from apps.agent.src.agent_orchestration.plugins.blackboard import (
    BlackboardRegionUpdatedEvent, RegionDefinition, RegionRegistry,
)
from apps.agent.src.agent_orchestration.plugins.memory.models import (
    MemoryHistoryItem, MemoryItem, MemoryRecallResult, MemoryRecord,
)
from apps.agent.src.agent_orchestration.plugins.memory.plugin import (
    MemoryOperationError, MemoryPlugin, _bound_items,
)
from apps.agent.src.agent_orchestration.plugins.user_input import UserInputEvent
from apps.agent.src.agent_orchestration.run_control import (
    TaskContextInputEvent, TaskContextInputResultEvent,
)


class BackendStub:
    def __init__(self, items=(), delay=0, error=None):
        self.items = tuple(items)
        self.delay = delay
        self.error = error
        self.calls = []
        self.records = {}
        self.closed = False

    def recall(self, query, **kwargs):
        self.calls.append(("recall", query, kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return MemoryRecallResult(self.items, query)

    async def arecall(self, query, **kwargs):
        self.calls.append(("arecall", query, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return MemoryRecallResult(self.items, query)

    def get(self, ref):
        self.calls.append(("get", ref))
        return self.records[ref]

    def history(self, ref):
        self.calls.append(("history", ref))
        return (MemoryHistoryItem("UPDATE", "new", "now"),)

    def remember(self, content, **kwargs):
        self.calls.append(("remember", content, kwargs))
        return tuple(self.records.values())

    def correct(self, ref, content):
        self.calls.append(("correct", ref, content))
        return self.records[ref]

    def set_expiration(self, ref, value):
        self.calls.append(("expiration", ref, value))
        return self.records[ref]

    def delete(self, ref):
        self.calls.append(("delete", ref))

    def close(self):
        self.closed = True

    async def aclose(self):
        pass


def item(ref="memory:1", content="remembered"):
    return MemoryItem(ref, content, 0.9, "global", "created", "updated")


def record(ref="memory:1", *, user="u", agent="a", run="global"):
    return MemoryRecord(item(ref), user, agent, run)


def plugin(
    backend, *, deadline_ms=1000, max_context_chars=6000,
    top_k=3, threshold=0.65,
):
    registry = RegionRegistry()
    handle = registry.register(RegionDefinition(
        "memory", "memory", "input", required_for_start=True,
        allowed_statuses=("idle", "recalling"),
    ))
    return MemoryPlugin(
        "memory", backend, workspace_key="wk", user_id="u", agent_id="a",
        session_id="s", region_registration=handle, deadline_ms=deadline_ms,
        max_context_chars=max_context_chars, top_k=top_k, threshold=threshold,
    )


def bind_background(target, published):
    async def publish(event):
        published.append(event)
        if isinstance(event, TaskContextInputEvent):
            await target.consume(
                "agent",
                TaskContextInputResultEvent(
                    task_id=event.task_id, request_event_id=event.event_id,
                    status="accepted",
                ),
            )

    target.bind_publisher(publish)
    target.bind_background_work_starter(
        lambda name, operation: asyncio.create_task(operation(), name=name)
    )


def test_automatic_recall先确认context再完成region():
    async def run():
        backend = BackendStub([item()])
        target = plugin(backend)
        published = []
        bind_background(target, published)
        event = UserInputEvent(task_id="task", prompt="hello")
        await target.consume("user-input", event)
        await target.drain()
        await asyncio.gather(*target._recall_tasks)
        return backend, published

    backend, events = asyncio.run(run())
    assert [type(event) for event in events] == [
        BlackboardRegionUpdatedEvent, TaskContextInputEvent,
        BlackboardRegionUpdatedEvent,
    ]
    assert events[-1].complete_for_input is True
    assert backend.calls[0][0] == "arecall"
    assert backend.calls[0][2]["scope"] is None
    assert backend.calls[0][2]["include_stopped"] is False


@pytest.mark.parametrize(
    "backend, expected_error",
    [(BackendStub(()), None), (BackendStub(error=RuntimeError("boom")), "backend_unavailable")],
)
def test_automatic_recall空或失败都不注入并释放门闩(backend, expected_error):
    async def run():
        target = plugin(backend)
        events = []
        bind_background(target, events)
        await target.consume("user-input", UserInputEvent(task_id="task", prompt="q"))
        await asyncio.gather(*target._recall_tasks)
        return events

    events = asyncio.run(run())
    assert not any(isinstance(event, TaskContextInputEvent) for event in events)
    assert events[-1].complete_for_input is True
    assert events[-1].output.error == expected_error


def test_automatic_recall超时在截止时间内完成且迟到结果不注入():
    async def run():
        target = plugin(BackendStub([item()], delay=0.2), deadline_ms=80)
        events = []
        bind_background(target, events)
        started = time.perf_counter()
        await target.consume("user-input", UserInputEvent(task_id="task", prompt="q"))
        await asyncio.gather(*target._recall_tasks)
        return time.perf_counter() - started, events

    elapsed, events = asyncio.run(run())
    assert elapsed < 0.15
    assert events[-1].output.error == "timeout"
    assert not any(isinstance(event, TaskContextInputEvent) for event in events)


def test_context注入未接受时终态降级且不公开记忆refs():
    async def run():
        target = plugin(BackendStub([item()]))
        events = []

        async def publish(event):
            events.append(event)
            if isinstance(event, TaskContextInputEvent):
                await target.consume(
                    "agent",
                    TaskContextInputResultEvent(
                        task_id=event.task_id,
                        request_event_id=event.event_id,
                        status="already_finished",
                    ),
                )

        target.bind_publisher(publish)
        target.bind_background_work_starter(
            lambda name, operation: asyncio.create_task(operation(), name=name)
        )
        await target.consume(
            "user-input", UserInputEvent(task_id="task", prompt="q")
        )
        await asyncio.gather(*target._recall_tasks)
        return events

    events = asyncio.run(run())
    assert events[-1].complete_for_input is True
    assert events[-1].output.error == "context_rejected"
    assert events[-1].output.refs == ()


def test_explicit操作校验归属并映射维护语义():
    backend = BackendStub()
    backend.records["memory:1"] = record()
    target = plugin(backend)
    assert target.get("memory:1")["content"] == "remembered"
    target.history("memory:1")
    target.correct("memory:1", "fixed")
    target.stop_reference("memory:1")
    target.restore_reference("memory:1")
    assert target.delete("memory:1") == {"ref": "memory:1", "deleted": True, "purged": False}
    assert ("expiration", "memory:1", "1970-01-01") in backend.calls
    assert ("expiration", "memory:1", None) in backend.calls

    backend.records["memory:other"] = record("memory:other", user="other")
    with pytest.raises(MemoryOperationError, match="does not belong"):
        target.delete("memory:other")


def test_snapshot预算按完整序列化item整条裁剪():
    selected, truncated = _bound_items(
        (item("memory:1", "a" * 60), item("memory:2", "b" * 60)),
        3, 260,
    )
    assert len(selected) == 1
    assert truncated is True


def test_explicit_recall同task完全相同指纹复用自动结果():
    backend = BackendStub([item()])
    target = plugin(backend)
    fingerprint = ("same query", None, False, 3, 0.65, 6000)
    target._remember_automatic_result(
        "task", fingerprint, MemoryRecallResult((item(),), "same query")
    )

    result = target.recall(
        " same   query ", scope=None, include_stopped=False,
        top_k=3, threshold=0.65, max_context_chars=6000, task_id="task",
    )

    assert result["items"][0]["ref"] == "memory:1"
    assert backend.calls == []


def test_explicit_recall不同指纹重新查询():
    backend = BackendStub([item()])
    target = plugin(backend)
    target._remember_automatic_result(
        "task", ("query", None, False, 3, 0.65, 6000),
        MemoryRecallResult((item(),), "query"),
    )

    target.recall(
        "different", scope=None, include_stopped=False,
        top_k=3, threshold=0.65, max_context_chars=6000, task_id="task",
    )

    assert backend.calls[0][0] == "recall"
