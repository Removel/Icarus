"""Plugin Runtime 通信与状态类型。"""

from dataclasses import dataclass, field
from datetime import datetime
import enum
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias
from uuid import uuid4

from apps.agent.src.agent_orchestration.events import Event


PluginId: TypeAlias = str
RuntimeHostStatus: TypeAlias = Literal[
    "created",
    "discovering",
    "resolving",
    "validating",
    "starting",
    "restoring",
    "ready",
    "running",
    "quiescing",
    "snapshotting",
    "stopping",
    "stopped",
    "failed",
]


class PluginStatus(str, enum.Enum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class PublishedEvent:
    source_plugin_id: PluginId
    event: Event
    hook_run_id: str | None = None
    hook_context: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hook_context",
            MappingProxyType(dict(self.hook_context)),
        )


@dataclass(frozen=True)
class Subscription:
    source_plugin_id: PluginId
    subscriber_plugin_id: PluginId
    subscription_id: str = ""

    def __post_init__(self) -> None:
        if not self.subscription_id:
            object.__setattr__(self, "subscription_id", uuid4().hex)


@dataclass(frozen=True)
class BackgroundWorkSnapshot:
    work_id: str
    name: str
    started_at: datetime


@dataclass(frozen=True)
class PluginRuntimeSnapshot:
    plugin_id: PluginId
    status: PluginStatus
    queue_size: int
    queue_capacity: int
    processed_count: int
    failed_count: int
    last_event_at: datetime | None
    last_error: str | None
    pending_count: int = 0
    background_work_count: int = 0
    active_background_works: tuple[BackgroundWorkSnapshot, ...] = ()
    last_background_work_at: datetime | None = None
    background_failed_count: int = 0
    last_background_error: str | None = None


@dataclass(frozen=True)
class RuntimePluginSnapshot:
    plugin_id: str
    plugin_version: str
    manifest_hash: str
    source: str
    state_scopes: tuple[str, ...]
    workspace_state_version: int | None
    session_state_version: int | None


@dataclass(frozen=True)
class RuntimeGraphSnapshot:
    workspace_path: str
    session_id: str
    plugins: tuple[RuntimePluginSnapshot, ...]
    disabled_plugin_ids: tuple[str, ...]
    capabilities: tuple[tuple[str, str], ...]
    capability_bindings: tuple[tuple[str, str, str], ...]
    tools: tuple[tuple[str, str], ...]
    subscriptions: tuple[tuple[str, str], ...]
    start_order: tuple[str, ...]
    stop_order: tuple[str, ...]
    diagnostics: tuple[tuple[str, str, str, str], ...]
