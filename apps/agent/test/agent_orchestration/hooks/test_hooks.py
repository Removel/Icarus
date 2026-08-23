import asyncio

from apps.agent.src.agent_orchestration.hooks import (
    BaseHook,
    HookDispatcher,
    HookEvent,
    HookRegistry,
    get_hook_context,
    hook_context,
)


class RecordingHook(BaseHook):
    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    def handle(self, event: HookEvent) -> None:
        self.events.append(event)


class FailingHook(BaseHook):
    def handle(self, event: HookEvent) -> None:
        raise RuntimeError("hook failed")


def test_hook_registry_支持多个handler和通配handler():
    registry = HookRegistry()
    direct = RecordingHook()
    wildcard = RecordingHook()

    assert registry.register("agent.invoke", direct) is True
    assert registry.register("*", wildcard) is True

    assert registry.get_hooks("agent.invoke") == [direct, wildcard]
    assert registry.get_hooks("missing") == [wildcard]
    assert registry.get_hooks("*") == [wildcard]


def test_hook_dispatcher_隔离handler异常且保持运行上下文(caplog):
    registry = HookRegistry()
    recorder = RecordingHook()
    registry.register("custom.event", FailingHook())
    registry.register("custom.event", recorder)
    dispatcher = HookDispatcher(registry)

    with hook_context({"model_role": "thinking"}) as context:
        dispatcher.trigger("custom.event", "after", {"value": 1})

    assert len(recorder.events) == 1
    assert recorder.events[0].run_id == context.run_id
    assert recorder.events[0].context["model_role"] == "thinking"
    assert recorder.events[0].data["value"] == 1
    assert "Hook handler failed" in caplog.text


def test_hook_dispatcher_未注册事件为空操作():
    HookDispatcher(HookRegistry()).trigger("missing", "before", {"value": 1})


def test_hook_context_并发异步运行互不污染():
    registry = HookRegistry()
    recorder = RecordingHook()
    registry.register("custom.event", recorder)
    dispatcher = HookDispatcher(registry)

    async def emit(model_role):
        with hook_context({"model_role": model_role}) as context:
            await asyncio.sleep(0)
            await dispatcher.atrigger("custom.event", "after", {})
            return context.run_id

    async def run():
        return await asyncio.gather(emit("thinking"), emit("perception"))

    run_ids = asyncio.run(run())

    assert len(set(run_ids)) == 2
    assert {event.run_id for event in recorder.events} == set(run_ids)


def test_hook_event_将数据转换为不可变快照():
    source = {"items": [1]}
    event = HookEvent.create("event", "before", "run-1", source)
    source["items"].append(2)

    assert event.data["items"] == [1]


def test_hook_context_嵌套合并并可生成新run():
    with hook_context(
        {
            "workspace_key": "workspace",
            "session_id": "session-1",
        },
        run_id=None,
    ) as session_context:
        with hook_context(
            {
                "task_id": "task-1",
                "model_role": "thinking",
            },
            new_run=True,
        ) as run_context:
            assert run_context.data == {
                "workspace_key": "workspace",
                "session_id": "session-1",
                "task_id": "task-1",
                "model_role": "thinking",
            }
            assert run_context.run_id
            assert run_context.run_id != session_context.run_id

        restored = get_hook_context()
        assert restored is session_context
        assert restored.run_id is None
