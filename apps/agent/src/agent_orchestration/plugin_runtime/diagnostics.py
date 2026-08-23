"""Structured diagnostics produced while building a Runtime graph."""

from dataclasses import dataclass, field
from typing import Literal


DiagnosticLevel = Literal["warning", "error"]


@dataclass(frozen=True)
class PluginDiagnostic:
    plugin_id: str
    code: str
    message: str
    level: DiagnosticLevel = "error"


@dataclass
class RuntimeDiagnostics:
    items: list[PluginDiagnostic] = field(default_factory=list)

    def add(
        self,
        plugin_id: str,
        code: str,
        message: str,
        *,
        level: DiagnosticLevel = "error",
    ) -> None:
        self.items.append(PluginDiagnostic(plugin_id, code, message, level))

    def for_plugin(self, plugin_id: str) -> tuple[PluginDiagnostic, ...]:
        return tuple(item for item in self.items if item.plugin_id == plugin_id)
