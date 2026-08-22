from dataclasses import asdict
from types import MappingProxyType

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRuntimeSnapshot,
    PluginStatus,
    PublishedEvent,
    Subscription,
)

from apps.agent.test.agent_orchestration.plugin_runtime.support import SampleEvent


def test_plugin_runtime_types_保持不可变和来源分离():
    event = SampleEvent(value="hello")
    published = PublishedEvent("producer", event)
    subscription = Subscription("producer", "consumer")
    snapshot = PluginRuntimeSnapshot(
        plugin_id="consumer",
        status=PluginStatus.RUNNING,
        queue_size=1,
        queue_capacity=0,
        processed_count=2,
        failed_count=1,
        last_event_at=None,
        last_error=None,
    )

    assert published.source_plugin_id == "producer"
    assert published.event is event
    assert published.hook_context == {}
    assert isinstance(published.hook_context, MappingProxyType)
    assert subscription.subscription_id
    assert asdict(snapshot)["status"] == PluginStatus.RUNNING
