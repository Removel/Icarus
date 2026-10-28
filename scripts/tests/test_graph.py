from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

from registry import Component, Dependency, Registry


def component(name, requires=(), **kwargs):
    defaults = dict(kind="app", mode="local", provisioning="process")
    defaults.update(kwargs)
    return Component(name=name, requires={dep: Dependency(dep) for dep in requires}, **defaults)


def test_required_closure_orders_shared_dependencies_once():
    registry = Registry(1, {
        "app": component("app", ("db", "cache")),
        "db": component("db", ("base",)),
        "cache": component("cache", ("base",)),
        "base": component("base"),
    })
    from graph import required_closure

    order = required_closure(registry, ["app", "db", "app"])
    assert order == ("base", "db", "cache", "app")


@pytest.fixture
def api():
    import graph

    return graph


def test_topological_order_and_reverse_are_stable(api):
    registry = Registry(1, {
        "a": component("a", ("shared",)),
        "b": component("b", ("shared",)),
        "shared": component("shared"),
    })
    order = api.topological_order(registry, ["b", "a", "b"])
    assert order == ("shared", "b", "a")
    assert api.reverse_order(order) == ("a", "b", "shared")
    assert api.required_closure(registry, []) == ()


def test_optional_edges_do_not_expand_or_create_cycles(api):
    app = component("app", ("db",))
    app.requires["db"] = Dependency("db", required=False)
    registry = Registry(1, {"app": app, "db": component("db", ("app",), enabled=False)})
    assert api.required_closure(registry, ["app"]) == ("app",)


def test_remote_component_stops_all_dependency_traversal(api):
    registry = Registry(1, {
        "app": component("app", ("service",)),
        "service": component("service", ("missing",), mode="remote"),
    })
    assert api.topological_order(registry, ["app"]) == ("service", "app")


@pytest.mark.parametrize("roots", [["missing"], ["app"]])
def test_unknown_root_or_required_reference_names_chain(api, roots):
    registry = Registry(1, {"app": component("app", ("missing",))})
    with pytest.raises(api.GraphError, match="unknown.*missing") as caught:
        api.required_closure(registry, roots)
    if roots == ["app"]:
        assert "app" in str(caught.value)


@pytest.mark.parametrize("roots", [["db"], ["app"]])
def test_disabled_required_components_fail_before_schedule(api, roots):
    registry = Registry(1, {"app": component("app", ("db",)), "db": component("db", enabled=False)})
    with pytest.raises(api.GraphError, match="disabled.*db"):
        api.topological_order(registry, roots)


def test_required_cycle_reports_component_chain(api):
    registry = Registry(1, {"a": component("a", ("b",)), "b": component("b", ("a",))})
    with pytest.raises(api.GraphError, match="cycle") as caught:
        api.topological_order(registry, ["a"])
    assert "a -> b -> a" in str(caught.value)


def test_embedded_dependencies_precede_owner_without_containment_edge(api):
    registry = Registry(1, {
        "gateway": component("gateway", ("agent",)),
        "agent": component("agent", ("mem0", "openkb"), provisioning="embedded", owner="gateway"),
        "mem0": component("mem0"),
        "openkb": component("openkb"),
    })
    assert api.topological_order(registry, ["gateway"]) == ("mem0", "openkb", "agent", "gateway")
    assert api.required_closure(registry, ["agent"]) == ("mem0", "openkb", "agent")


def test_bundled_own_owner_is_not_prestarted_or_a_cycle(api):
    registry = Registry(1, {
        "mem0": component("mem0", ("postgres", "cache")),
        "postgres": component("postgres", provisioning="bundled", owner="mem0"),
        "cache": component("cache"),
        "other": component("other", ("postgres",)),
    })
    assert api.topological_order(registry, ["mem0"]) == ("cache", "mem0")
    assert api.required_closure(registry, ["postgres"]) == ("cache", "mem0")
    assert api.topological_order(registry, ["other", "postgres"]) == ("cache", "mem0", "other")


@pytest.mark.parametrize("nested_owner", [False, True])
def test_embedded_consumer_skips_own_runtime_bundled_database(api, nested_owner):
    registry = Registry(1, {
        "gateway": component("gateway", ("agent",)),
        "agent": component("agent", ("db",), provisioning="embedded",
                           owner="middle" if nested_owner else "gateway"),
        "middle": component("middle", provisioning="embedded", owner="gateway"),
        "db": component("db", provisioning="bundled", owner="gateway"),
    })
    assert api.topological_order(registry, ["gateway"]) == ("agent", "gateway")
    assert api.required_closure(registry, ["agent"]) == ("agent",)


def test_bundled_normalization_does_not_hide_real_execution_cycle(api):
    registry = Registry(1, {
        "a": component("a", ("db",)),
        "b": component("b", ("a",)),
        "db": component("db", provisioning="bundled", owner="b"),
    })
    with pytest.raises(api.GraphError, match="cycle") as caught:
        api.topological_order(registry, ["a"])
    assert "a" in str(caught.value) and "b" in str(caught.value)


@pytest.mark.parametrize("provisioning", ["embedded", "bundled"])
def test_hosted_ownership_cycles_are_descriptive(api, provisioning):
    registry = Registry(1, {
        "a": component("a", provisioning=provisioning, owner="b"),
        "b": component("b", provisioning=provisioning, owner="a"),
    })
    with pytest.raises(api.GraphError, match="ownership cycle") as caught:
        api.required_closure(registry, ["a"])
    assert "a -> b -> a" in str(caught.value)


def test_embedded_unknown_owner_is_rejected(api):
    registry = Registry(1, {"agent": component("agent", provisioning="embedded", owner="missing")})
    with pytest.raises(api.GraphError, match="unknown.*missing") as caught:
        api.required_closure(registry, ["agent"])
    assert "agent" in str(caught.value)


def test_remote_bundled_component_is_not_normalized_to_local_owner(api):
    remote = component("db", ("missing",), mode="remote", provisioning="bundled", owner="service")
    registry = Registry(1, {"db": remote, "service": component("service")})
    assert api.topological_order(registry, ["db"]) == ("db",)


@pytest.mark.parametrize("nested_owner", [False, True])
def test_remote_owner_makes_default_embedded_agent_opaque(api, nested_owner):
    registry = Registry(1, {
        "gateway": component("gateway", ("agent",), mode="remote", default_stack=True),
        "agent": component("agent", ("mem0", "openkb"), provisioning="embedded",
                           owner="middle" if nested_owner else "gateway", default_stack=True),
        "middle": component("middle", provisioning="embedded", owner="gateway"),
        "mem0": component("mem0"),
        "openkb": component("openkb"),
    })
    assert api.required_closure(registry, registry.default_stack()) == ("gateway", "agent")
    assert api.topological_order(registry, ["agent"]) == ("agent",)


def test_bundled_request_under_remote_owner_skips_local_internals(api):
    registry = Registry(1, {
        "mem0": component("mem0", ("postgres", "cache"), mode="remote"),
        "postgres": component("postgres", provisioning="bundled", owner="mem0"),
        "cache": component("cache"),
    })
    assert api.required_closure(registry, ["postgres"]) == ("mem0",)


def test_graph_does_not_mutate_registry(api):
    app = component("app", ("db",))
    database = component("db")
    registry = Registry(1, {"app": app, "db": database})
    assert api.required_closure(registry, ["app"]) == ("db", "app")
    assert app == replace(app, requires={"db": Dependency("db")})
    assert registry.names() == ("app", "db")
