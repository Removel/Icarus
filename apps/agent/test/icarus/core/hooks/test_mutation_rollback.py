"""Capability/handler contracts stay bounded when hooks operate on mutable payloads."""

from dataclasses import dataclass
from icarus.core.hooks import BaseHook, HookDispatcher, HookRegistry


@dataclass(frozen=True)
class Payload:
    values: list[str]


def test_hook_mutation_then_failure_does_not_leak_into_original_or_previous_value():
    registry = HookRegistry()
    dispatcher = HookDispatcher(registry)
    dispatcher.declare_point("mutable", "before", Payload)
    class Bad(BaseHook):
        def handle(self, event, value):
            value.values.append("bad")
            raise RuntimeError("failed after mutation")
    registry.register("mutable", Bad(), priority=0)
    original = Payload(["safe"])
    result = dispatcher.trigger("mutable", "before", original)
    assert result.values == ["safe"]
    assert original.values == ["safe"]
