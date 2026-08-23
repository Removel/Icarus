"""Discover Plugin manifests without importing Plugin implementation code."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Iterable

from apps.agent.src.agent_orchestration.plugin_runtime.diagnostics import (
    RuntimeDiagnostics,
)
from apps.agent.src.agent_orchestration.plugin_runtime.manifest import (
    PluginManifest,
    parse_manifest_text,
)


@dataclass(frozen=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    manifest_hash: str
    source: str
    built_in: bool
    import_root: Path | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    plugins: tuple[DiscoveredPlugin, ...]
    diagnostics: RuntimeDiagnostics


class PluginManifestDiscovery:
    def __init__(
        self,
        plugin_dirs: Iterable[str | Path] = (),
        *,
        builtin_package: str = "apps.agent.src.agent_orchestration.plugins",
    ) -> None:
        self.plugin_dirs = tuple(
            dict.fromkeys(Path(path).expanduser().resolve() for path in plugin_dirs)
        )
        self.builtin_package = builtin_package

    def discover(self) -> DiscoveryResult:
        diagnostics = RuntimeDiagnostics()
        candidates: list[DiscoveredPlugin] = []
        builtin_root = files(self.builtin_package)
        candidates.extend(self._scan_root(builtin_root, True, None, diagnostics))
        for root in self.plugin_dirs:
            if not root.is_dir():
                diagnostics.add(str(root), "plugin_directory_missing", f"Plugin directory is not found: {root}")
                continue
            candidates.extend(self._scan_root(root, False, root, diagnostics))

        grouped: dict[str, list[DiscoveredPlugin]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.manifest.plugin_id, []).append(candidate)

        selected: list[DiscoveredPlugin] = []
        for plugin_id, group in sorted(grouped.items()):
            builtins = [item for item in group if item.built_in]
            externals = [item for item in group if not item.built_in]
            if len(builtins) > 1:
                diagnostics.add(plugin_id, "duplicate_builtin_plugin", f"Duplicate built-in Plugin ID: {plugin_id}")
                continue
            if builtins:
                selected.append(builtins[0])
                for external in externals:
                    diagnostics.add(plugin_id, "builtin_override_rejected", f"External Plugin cannot override built-in Plugin {plugin_id}: {external.source}")
                continue
            if len(externals) > 1:
                diagnostics.add(plugin_id, "duplicate_external_plugin", f"Duplicate external Plugin ID: {plugin_id}")
                continue
            selected.extend(externals)
        return DiscoveryResult(tuple(selected), diagnostics)

    def _scan_root(
        self,
        root: Traversable | Path,
        built_in: bool,
        import_root: Path | None,
        diagnostics: RuntimeDiagnostics,
    ) -> list[DiscoveredPlugin]:
        discovered: list[DiscoveredPlugin] = []
        for child in sorted(root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            manifest_file = child.joinpath("manifest.json")
            if not manifest_file.is_file():
                continue
            try:
                text = manifest_file.read_text(encoding="utf-8")
                manifest, manifest_hash = parse_manifest_text(text)
                if manifest.plugin_id != child.name.replace("_", "-"):
                    raise ValueError("plugin_id must match its directory name")
                if not built_in:
                    module_name = manifest.entrypoint.split(":", 1)[0]
                    package_name = module_name.split(".", 1)[0]
                    expected_package = manifest.plugin_id.replace("-", "_")
                    if package_name != expected_package:
                        raise ValueError(
                            "external Plugin entrypoint package must match "
                            "plugin_id with hyphens replaced by underscores"
                        )
            except Exception as error:
                diagnostics.add(child.name, "invalid_manifest", f"Invalid Plugin manifest {manifest_file}: {error}")
                continue
            discovered.append(
                DiscoveredPlugin(
                    manifest=manifest,
                    manifest_hash=manifest_hash,
                    source=str(manifest_file),
                    built_in=built_in,
                    import_root=(
                        Path(child)
                        if import_root is not None
                        else None
                    ),
                )
            )
        return discovered
