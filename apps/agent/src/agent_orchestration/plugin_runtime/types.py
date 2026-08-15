"""Plugin Runtime 通信与状态类型。"""

from dataclasses import dataclass
from datetime import datetime
import enum
from typing import TypeAlias
from uuid import uuid4

from apps.agent.src.agent_orchestration.events import Event


PluginId: TypeAlias = str


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


@dataclass(frozen=True)
class Subscription:
    source_plugin_id: PluginId
    subscriber_plugin_id: PluginId
    subscription_id: str = ""

    def __post_init__(self) -> None:
        if not self.subscription_id:
            object.__setattr__(self, "subscription_id", uuid4().hex)


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
