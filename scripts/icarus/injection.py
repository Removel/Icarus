"""Pure child-environment wiring; callers merge dotenv before calling here."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from registry import Component, Registry, RegistryError


@dataclass(frozen=True)
class InjectionResult:
    environment: dict[str, str]
    warnings: tuple[str, ...]


def _runtime_consumer(component: Component, registry: Registry) -> Component:
    chain: list[str] = []
    while component.mode == "local" and component.provisioning in {"embedded", "bundled"}:
        if component.name in chain:
            raise RegistryError(f"ownership cycle: {' -> '.join((*chain, component.name))}")
        chain.append(component.name)
        if component.owner is None:
            raise RegistryError(f"missing owner for {component.name}")
        component = registry.get(component.owner)
    return component


def build_child_environment(
    component: Component, registry: Registry, base_environment: Mapping[str, str],
) -> InjectionResult:
    """Export own metadata and declared dependency wiring without mutating inputs.

    Registry exports override inherited values, with names-only warnings. Two
    registry exports for the same key must agree; otherwise wiring is ambiguous.
    Secret references resolve only from base_environment, never another export.
    Embedded exports are returned separately for lifecycle to union into owner.
    """
    environment = dict(base_environment)
    warnings: list[str] = []
    exports: dict[str, tuple[str, str]] = {}
    runtime = _runtime_consumer(component, registry)

    def export(key: str | None, value: str | int | bool | None, source: str) -> None:
        if key is None or value is None:
            return
        value = str(value).lower() if type(value) is bool else str(value)
        if key in exports:
            previous, previous_source = exports[key]
            if previous != value:
                raise RegistryError(
                    f"{component.name}: conflicting export {key} from {previous_source} and {source}"
                )
            return
        exports[key] = value, source
        if key in base_environment and base_environment[key] != value:
            warnings.append(f"{component.name}: overriding {key} for {source}")
        environment[key] = value

    for key, value in component.env.items():
        export(key, value, component.name)
    for destination, source in component.secret_env.items():
        value = base_environment.get(source)
        if not value:
            raise RegistryError(f"{component.name}: missing secret environment {source} for {destination}")
        export(destination, value, component.name)
    export(component.host_env, component.bind_host or component.host, component.name)
    export(component.port_env, component.port, component.name)
    export(component.endpoint_env, component.endpoint, component.name)

    for dependency in component.requires.values():
        target = registry.get(dependency.name)
        export(dependency.endpoint_env, target.endpoint, target.name)
        connection = dependency.connection
        if connection is None:
            continue
        use_network = (
            runtime.mode == "local" and runtime.provisioning == "compose"
            and target.mode == "local" and runtime.shared_network and target.shared_network
            and runtime.network == target.network and target.network_host is not None
        )
        host = target.network_host if use_network else target.host
        port = (target.network_port or target.port) if use_network else target.port
        export(connection.host_env, host, target.name)
        export(connection.port_env, port, target.name)
        for field in ("database", "user", "schema", "graph"):
            export(getattr(connection, f"{field}_env"), getattr(connection, field), target.name)
        # password_env is a binding name only: preserve inheritance, never derive it.

    return InjectionResult(environment, tuple(warnings))
