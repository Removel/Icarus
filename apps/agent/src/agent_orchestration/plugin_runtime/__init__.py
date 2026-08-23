"""Agent 编排层的 Plugin Runtime。"""

from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.plugin_runtime.event_bus import EventBus
from apps.agent.src.agent_orchestration.plugin_runtime.diagnostics import (
    PluginDiagnostic,
    RuntimeDiagnostics,
)
from apps.agent.src.agent_orchestration.plugin_runtime.discovery import (
    DiscoveredPlugin,
    DiscoveryResult,
    PluginManifestDiscovery,
)
from apps.agent.src.agent_orchestration.plugin_runtime.manifest import (
    PluginManifest,
    ProvidedCapabilityManifest,
    RequiredCapabilityManifest,
)
from apps.agent.src.agent_orchestration.plugin_runtime.host import (
    PluginRuntimeHost,
)
from apps.agent.src.agent_orchestration.plugin_runtime.graph_builder import (
    PluginGraphBuilder,
)
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_manager import (
    PluginManager,
)
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_registry import (
    PluginRegistry,
)
from apps.agent.src.agent_orchestration.plugin_runtime.plugin_runtime import (
    PluginRuntime,
)
from apps.agent.src.agent_orchestration.plugin_runtime.registration import (
    PluginRegistration,
    PluginStateProvider,
    PluginStateStore,
    ProvidedCapability,
)
from apps.agent.src.agent_orchestration.plugin_runtime.resolver import (
    RequiredPluginError,
    ResolvedPluginGraph,
    resolve_plugins,
)
from apps.agent.src.agent_orchestration.plugin_runtime.state_coordinator import (
    PluginStateCoordinator,
)
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    PluginId,
    PluginRuntimeSnapshot,
    PluginStatus,
    PublishedEvent,
    RuntimeGraphSnapshot,
    RuntimeHostStatus,
    RuntimePluginSnapshot,
    Subscription,
)

__all__ = [
    "BasePlugin",
    "DiscoveredPlugin",
    "DiscoveryResult",
    "EventBus",
    "PluginDiagnostic",
    "PluginGraphBuilder",
    "PluginId",
    "PluginManifest",
    "PluginManifestDiscovery",
    "PluginManager",
    "PluginRegistration",
    "PluginRuntimeHost",
    "PluginRegistry",
    "PluginRuntime",
    "PluginRuntimeSnapshot",
    "PluginStateProvider",
    "PluginStateStore",
    "PluginStateCoordinator",
    "PluginStatus",
    "ProvidedCapability",
    "ProvidedCapabilityManifest",
    "PublishedEvent",
    "RequiredCapabilityManifest",
    "RequiredPluginError",
    "ResolvedPluginGraph",
    "RuntimeDiagnostics",
    "RuntimeGraphSnapshot",
    "RuntimeHostStatus",
    "RuntimePluginSnapshot",
    "Subscription",
    "resolve_plugins",
]
