"""Deterministic required-dependency scheduling; containment is not a DAG edge."""
from __future__ import annotations

from collections.abc import Sequence

from registry import Component, Registry


class GraphError(ValueError):
    """A requested component cannot form a valid local execution order."""


def topological_order(registry: Registry, names: Sequence[str]) -> tuple[str, ...]:
    """Dependencies first, retaining embedded nodes and normalizing bundled owners.

    Remote nodes and nodes contained by a remote owner are opaque. A bundled
    dependency owned by the current consumer is supplied by that consumer, not
    a prerequisite. Other bundled requests resolve to their owner; lifecycle
    inspects bundled readiness after startup.
    """
    order: list[str] = []
    active: list[str] = []
    done: set[str] = set()

    def get(name: str, chain: Sequence[str]) -> Component:
        component = registry.components.get(name)
        context = " -> ".join((*chain, name))
        if component is None:
            raise GraphError(f"unknown component {name} in {context}")
        if not component.enabled:
            raise GraphError(f"disabled component {name} in {context}")
        return component

    def runtime_owner(component: Component) -> Component:
        chain: list[str] = []
        while component.mode != "remote" and component.provisioning in {"embedded", "bundled"}:
            if component.name in chain:
                raise GraphError(f"ownership cycle: {' -> '.join((*chain, component.name))}")
            chain.append(component.name)
            if component.owner is None:
                raise GraphError(f"missing owner for {component.name}")
            component = get(component.owner, chain)
        return component

    def visit(name: str) -> None:
        component = get(name, active)
        owner = runtime_owner(component)
        bundled = component.mode == "local" and component.provisioning == "bundled"
        while component.mode == "local" and component.provisioning == "bundled":
            component = get(component.owner, (*active, component.name))
        name = component.name
        if bundled and active and owner.name == runtime_owner(get(active[-1], active)).name:
            return
        if name in active:
            raise GraphError(f"dependency cycle: {' -> '.join((*active, name))}")
        if name in done:
            return
        active.append(name)
        if owner.mode != "remote":
            for dependency in component.requires.values():
                if dependency.required:
                    visit(dependency.name)
        active.pop()
        done.add(name)
        order.append(name)

    for name in names:
        visit(name)
    return tuple(order)


def required_closure(registry: Registry, names: Sequence[str]) -> tuple[str, ...]:
    """Return the stable deps-first closure, with the same validation as ordering."""
    return topological_order(registry, names)


def reverse_order(order: Sequence[str]) -> tuple[str, ...]:
    """Reverse a start order for shutdown; schedules already contain unique nodes."""
    return tuple(reversed(order))
