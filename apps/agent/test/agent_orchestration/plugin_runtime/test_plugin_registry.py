import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRegistry,
    PluginStatus,
)

from apps.agent.test.agent_orchestration.plugin_runtime.support import RecordingPlugin


def test_plugin_registry_按来源维护订阅和注册顺序():
    registry = PluginRegistry()
    source = RecordingPlugin("source")
    first = RecordingPlugin("first")
    second = RecordingPlugin("second")
    for plugin in (source, first, second):
        registry.register(plugin)

    first_subscription = registry.subscribe("first", "source")
    duplicate = registry.subscribe("first", "source")
    registry.subscribe("second", "source")

    assert duplicate is first_subscription
    assert registry.get_subscriber_ids("source") == ["first", "second"]


def test_plugin_registry_要求双方注册并在注销时清理关系():
    registry = PluginRegistry()
    source = RecordingPlugin("source")
    consumer = RecordingPlugin("consumer")
    registry.register(source)

    with pytest.raises(KeyError, match="consumer"):
        registry.subscribe("consumer", "source")

    registry.register(consumer)
    registry.subscribe("consumer", "source")
    registry.set_status("consumer", PluginStatus.STOPPED)
    registry.unregister("consumer")

    assert registry.get_subscriber_ids("source") == []


def test_plugin_registry_拒绝注销运行中插件():
    registry = PluginRegistry()
    plugin = RecordingPlugin("plugin")
    registry.register(plugin)
    registry.set_status("plugin", PluginStatus.RUNNING)

    with pytest.raises(RuntimeError, match="cannot be unregistered"):
        registry.unregister("plugin")
