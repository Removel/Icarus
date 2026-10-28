"""Hook values flow in explicit order without mutating the caller's input."""

import asyncio
from dataclasses import dataclass

from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry


@dataclass(frozen=True)
class Value:
    text: str


def test_hook_values_are_ordered_and_failures_roll_back():
    registry = HookRegistry()
    dispatcher = HookDispatcher(registry)
    assert hasattr(dispatcher, "declare_point"), "typed value hook points are missing"
    dispatcher.declare_point("test.value", "before", Value)
    order = []
    class Rewrite(BaseHook):
        def __init__(self, name):
            self.name = name
        def handle(self, event, value):
            order.append(self.name)
            return Value(value.text + self.name)
    class Bad(BaseHook):
        def handle(self, event, value):
            raise RuntimeError("bad hook")
    registry.register("test.value", Rewrite("b"), priority=20)
    registry.register("*", Rewrite("a"), priority=10)
    registry.register("test.value", Bad(), priority=15)
    original = Value("start")
    assert dispatcher.trigger("test.value", "before", original).text == "startab"
    assert original.text == "start"
    assert order == ["a", "b"]


def test_async_hooks_execute_serially_and_preserve_previous_value():
    async def run():
        registry = HookRegistry()
        dispatcher = HookDispatcher(registry)
        assert hasattr(dispatcher, "declare_point"), "typed value hook points are missing"
        dispatcher.declare_point("test.async", "before", Value)
        seen = []
        class Rewrite(BaseHook):
            def handle(self, event, value):
                return value
            async def ahandle(self, event, value):
                seen.append(value.text)
                return Value(value.text + "x")
        registry.register("*", Rewrite(), priority=1)
        registry.register("test.async", Rewrite(), priority=2)
        assert (await dispatcher.atrigger("test.async", "before", Value("a"))).text == "axx"
        assert seen == ["a", "ax"]
    asyncio.run(run())
