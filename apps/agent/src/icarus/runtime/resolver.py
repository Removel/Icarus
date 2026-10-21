"""Resolve capability dependencies before importing any plugin implementation."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from icarus.runtime.diagnostics import RuntimeDiagnostics
from icarus.runtime.discovery import DiscoveredPlugin


class RequiredPluginError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedPluginGraph:
    plugins: tuple[DiscoveredPlugin, ...]
    disabled_plugin_ids: frozenset[str]
    diagnostics: RuntimeDiagnostics


def capability_providers(by_id, disabled=()):
    owners: dict[str, list[str]] = {}
    for plugin_id, item in by_id.items():
        if plugin_id in disabled:
            continue
        for capability in item.manifest.provided_capabilities:
            owners.setdefault(capability.capability_id, []).append(plugin_id)
    return owners


def resolve_plugins(plugins: tuple[DiscoveredPlugin, ...], *, required_plugin_ids: frozenset[str],
                    diagnostics: RuntimeDiagnostics | None = None) -> ResolvedPluginGraph:
    diagnostics = diagnostics or RuntimeDiagnostics()
    by_id = {item.manifest.plugin_id: item for item in plugins}
    required = set(required_plugin_ids) | {p for p, item in by_id.items() if item.manifest.tier == "base"}
    disabled = set(required - by_id.keys())
    for p in sorted(disabled):
        diagnostics.add(p, "required_plugin_missing", f"Required Plugin is not found: {p}")
    for p, item in by_id.items():
        errors = _python_requirement_errors(item.manifest.python_requires)
        if errors:
            disabled.add(p)
            diagnostics.add(p, "python_dependency_unavailable", "; ".join(errors))
    for capability, owners in capability_providers(by_id, disabled).items():
        if len(owners) > 1:
            for p in owners:
                disabled.add(p)
                diagnostics.add(p, "duplicate_capability", f"Capability has multiple providers: {capability}")
    tool_owners: dict[str, list[str]] = {}
    for p, item in by_id.items():
        if p not in disabled:
            for tool in item.manifest.provided_tools:
                tool_owners.setdefault(tool, []).append(p)
    for tool, owners in tool_owners.items():
        if len(owners) > 1:
            mandatory = set(owners) & required
            if len(mandatory) > 1:
                raise RequiredPluginError(f"Required Plugins provide duplicate Tool {tool}")
            for p in set(owners) - mandatory:
                disabled.add(p)
                diagnostics.add(p, "duplicate_tool", f"Tool has multiple providers: {tool}")
    _cascade(by_id, disabled, diagnostics)
    enabled = {p: item for p, item in by_id.items() if p not in disabled}
    providers = capability_providers(enabled)
    dependencies = {p: {providers[r.capability_id][0] for r in item.manifest.required_capabilities}
                    for p, item in enabled.items()}
    order = []
    while True:
        ready = sorted(p for p, deps in dependencies.items() if not deps and p not in order)
        if not ready:
            break
        for p in ready:
            order.append(p)
            for deps in dependencies.values():
                deps.discard(p)
    cycle = set(enabled) - set(order)
    if cycle:
        if cycle & required:
            raise RequiredPluginError("Capability dependency cycle contains required Plugin: " + ", ".join(sorted(cycle & required)))
        disabled.update(cycle)
        for p in cycle:
            diagnostics.add(p, "capability_dependency_cycle", "Capability dependency cycle")
        _cascade(by_id, disabled, diagnostics)
        order = [p for p in order if p not in disabled]
    if required & disabled:
        raise RequiredPluginError("Required Plugins are unavailable: " + ", ".join(sorted(required & disabled)))
    return ResolvedPluginGraph(tuple(by_id[p] for p in order), frozenset(disabled), diagnostics)


def _cascade(by_id, disabled, diagnostics):
    changed = True
    while changed:
        changed = False
        providers = capability_providers(by_id, disabled)
        published = {event for p, item in by_id.items() if p not in disabled for event in item.manifest.published_events}
        for p, item in by_id.items():
            if p in disabled:
                continue
            missing = []
            for requirement in item.manifest.required_capabilities:
                owners = providers.get(requirement.capability_id, [])
                version = by_id[owners[0]].manifest.capability_version(requirement.capability_id) if len(owners) == 1 else None
                if version is None or Version(version) not in SpecifierSet(requirement.version_spec):
                    missing.append(requirement.capability_id)
            absent_events = set(item.manifest.consumed_events) - set(item.manifest.optional_consumed_events) - published
            if missing or absent_events:
                disabled.add(p)
                diagnostics.add(p, "required_capability_unavailable" if missing else "event_publisher_unavailable",
                                "Unavailable contracts: " + ", ".join(sorted(missing or absent_events)))
                changed = True


def _python_requirement_errors(requirements: tuple[str, ...]) -> list[str]:
    errors = []
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
