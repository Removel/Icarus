"""Runtime capability provider bindings, independent of business plugin names."""

from packaging.specifiers import SpecifierSet
from packaging.version import Version


class CapabilityRegistry:
    def __init__(self) -> None:
        self._bindings: dict[str, tuple[str, str, object]] = {}

    def bind(self, capability_id: str, version: str, provider_plugin_id: str, value=None) -> None:
        if capability_id in self._bindings:
            raise ValueError(f"Duplicate capability provider: {capability_id}")
        self._bindings[capability_id] = (version, provider_plugin_id, value)

    def provider_for(self, capability_id: str, version_spec: str = "") -> str:
        version, provider, _ = self._bindings[capability_id]
        if Version(version) not in SpecifierSet(version_spec):
            raise KeyError(f"Capability version mismatch: {capability_id}")
        return provider

    def get_host_port(self, capability_id: str):
        return self._bindings[capability_id][2]

    def remove_provider(self, plugin_id: str) -> None:
        self._bindings = {k: v for k, v in self._bindings.items() if v[1] != plugin_id}

    def clear(self) -> None:
        self._bindings.clear()
