"""Resolve Plugin capability dependencies and deterministic load order."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from apps.agent.src.agent_orchestration.plugin_runtime.diagnostics import (
    RuntimeDiagnostics,
)
from apps.agent.src.agent_orchestration.plugin_runtime.discovery import (
    DiscoveredPlugin,
)


class RequiredPluginError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedPluginGraph:
    plugins: tuple[DiscoveredPlugin, ...]
    disabled_plugin_ids: frozenset[str]
    diagnostics: RuntimeDiagnostics


def resolve_plugins(
    plugins: tuple[DiscoveredPlugin, ...],
    *,
    required_plugin_ids: frozenset[str],
    diagnostics: RuntimeDiagnostics | None = None,
) -> ResolvedPluginGraph:
    diagnostics = diagnostics or RuntimeDiagnostics()
    by_id = {item.manifest.plugin_id: item for item in plugins}
    disabled: set[str] = set()

    for plugin_id in sorted(required_plugin_ids - by_id.keys()):
        diagnostics.add(plugin_id, "required_plugin_missing", f"Required Plugin is not found: {plugin_id}")
        disabled.add(plugin_id)

    for plugin_id, plugin in by_id.items():
        errors = _python_requirement_errors(plugin.manifest.python_requires)
        if errors:
            diagnostics.add(plugin_id, "python_dependency_unavailable", "; ".join(errors))
            disabled.add(plugin_id)

    changed = True
    while changed:
        changed = False
        for plugin_id, plugin in by_id.items():
            if plugin_id in disabled:
                continue
            for requirement in plugin.manifest.required_capabilities:
                provider = by_id.get(requirement.plugin_id)
                version = (
                    provider.manifest.capability_version(requirement.capability_id)
                    if provider is not None and requirement.plugin_id not in disabled
                    else None
                )
                if version is None or Version(version) not in SpecifierSet(requirement.version_spec):
                    diagnostics.add(
                        plugin_id,
                        "required_capability_unavailable",
                        f"Required capability is unavailable: {requirement.plugin_id}/{requirement.capability_id} {requirement.version_spec}",
                    )
                    disabled.add(plugin_id)
                    changed = True
                    break

    _disable_tool_conflicts(by_id, disabled, required_plugin_ids, diagnostics)
    _cascade_disabled(by_id, disabled, diagnostics)
    _disable_missing_event_publishers(by_id, disabled, diagnostics)
    _cascade_disabled(by_id, disabled, diagnostics)

    enabled = {key: value for key, value in by_id.items() if key not in disabled}
    order, cycle = _topological_order(enabled)
    if cycle:
        core = cycle.intersection(required_plugin_ids)
        if core:
            raise RequiredPluginError("Capability dependency cycle contains required Plugin: " + ", ".join(sorted(core)))
        for plugin_id in cycle:
            diagnostics.add(plugin_id, "capability_dependency_cycle", "Plugin is part of a capability dependency cycle")
            disabled.add(plugin_id)
        _cascade_disabled(by_id, disabled, diagnostics)
        enabled = {key: value for key, value in by_id.items() if key not in disabled}
        order, cycle = _topological_order(enabled)
        if cycle:
            raise RuntimeError("Unresolved Plugin dependency cycle")

    missing_core = sorted(required_plugin_ids.intersection(disabled))
    if missing_core:
        raise RequiredPluginError("Required Plugins are unavailable: " + ", ".join(missing_core))
    return ResolvedPluginGraph(
        plugins=tuple(enabled[plugin_id] for plugin_id in order),
        disabled_plugin_ids=frozenset(disabled),
        diagnostics=diagnostics,
    )


def _python_requirement_errors(requirements: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for text in requirements:
        requirement = Requirement(text)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"missing Python package: {requirement}")
            continue
        if installed not in requirement.specifier:
            errors.append(f"Python package version mismatch: {requirement.name} {installed} does not satisfy {requirement.specifier}")
    return errors


def _disable_tool_conflicts(by_id, disabled, required, diagnostics) -> None:
    owners: dict[str, list[str]] = {}
    for plugin_id, plugin in by_id.items():
        if plugin_id in disabled:
            continue
        for tool_name in plugin.manifest.provided_tools:
            owners.setdefault(tool_name, []).append(plugin_id)
    for tool_name, plugin_ids in owners.items():
        if len(plugin_ids) < 2:
            continue
        core = [plugin_id for plugin_id in plugin_ids if plugin_id in required]
        if len(core) > 1:
            raise RequiredPluginError(f"Required Plugins provide duplicate Tool {tool_name}: {', '.join(sorted(core))}")
        rejected = [plugin_id for plugin_id in plugin_ids if plugin_id not in core]
        for plugin_id in rejected:
            diagnostics.add(plugin_id, "duplicate_tool", f"Tool name is provided by multiple Plugins: {tool_name}")
            disabled.add(plugin_id)


def _cascade_disabled(by_id, disabled, diagnostics) -> None:
    changed = True
    while changed:
        changed = False
        for plugin_id, plugin in by_id.items():
            if plugin_id in disabled:
                continue
            failed_providers = {item.plugin_id for item in plugin.manifest.required_capabilities}.intersection(disabled)
            if failed_providers:
                diagnostics.add(plugin_id, "disabled_dependency", "Capability provider is disabled: " + ", ".join(sorted(failed_providers)))
                disabled.add(plugin_id)
                changed = True


def _disable_missing_event_publishers(by_id, disabled, diagnostics) -> None:
    changed = True
    while changed:
        changed = False
        published = {
            event
            for plugin_id, plugin in by_id.items()
            if plugin_id not in disabled
            for event in plugin.manifest.published_events
        }
        for plugin_id, plugin in by_id.items():
            if plugin_id in disabled:
                continue
            missing = sorted(
                set(plugin.manifest.consumed_events) - published
            )
            if not missing:
                continue
            diagnostics.add(
                plugin_id,
                "event_publisher_unavailable",
                "Consumed Events have no enabled publisher: "
                + ", ".join(missing),
            )
            disabled.add(plugin_id)
            changed = True


def _topological_order(by_id):
    dependencies = {
        plugin_id: {item.plugin_id for item in plugin.manifest.required_capabilities if item.plugin_id in by_id}
        for plugin_id, plugin in by_id.items()
    }
    order: list[str] = []
    def order_key(plugin_id):
        provides_state_store = any(
            capability.capability_id == "state_store"
            for capability in by_id[plugin_id].manifest.provided_capabilities
        )
        return (not provides_state_store, plugin_id)

    ready = sorted(
        (plugin_id for plugin_id, values in dependencies.items() if not values),
        key=order_key,
    )
    while ready:
        plugin_id = ready.pop(0)
        order.append(plugin_id)
        for candidate in sorted(dependencies):
            if plugin_id in dependencies[candidate]:
                dependencies[candidate].remove(plugin_id)
                if not dependencies[candidate] and candidate not in order and candidate not in ready:
                    ready.append(candidate)
                    ready.sort(key=order_key)
    cycle = {plugin_id for plugin_id, values in dependencies.items() if values}
    return order, cycle
