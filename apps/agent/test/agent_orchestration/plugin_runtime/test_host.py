import json
import sys

import pytest

from apps.agent.src.agent_orchestration.plugin_runtime import (
    PluginRuntimeHost,
    RequiredPluginError,
)
from apps.agent.test.agent_orchestration.plugin_runtime.support import (
    SampleEvent,
)


EVENT = (
    "apps.agent.test.agent_orchestration.plugin_runtime.support.SampleEvent"
)


def write_manifest(root, plugin_id, *, fail_factory=False, **changes):
    directory = root / plugin_id
    directory.mkdir(parents=True)
    package_name = plugin_id.replace("-", "_")
    package = directory / package_name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    if fail_factory:
        factory_code = (
            "def create_plugin(**kwargs):\n"
            "    raise RuntimeError('factory boom')\n"
        )
    else:
        factory_code = (
            "from apps.agent.test.agent_orchestration.plugin_runtime."
            "host_fixtures import create_plugin\n"
        )
    (package / "factory.py").write_text(
        factory_code, encoding="utf-8"
    )
    payload = {
        "schema_version": 1,
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "entrypoint": f"{package_name}.factory:create_plugin",
        "python_requires": [],
        "required_capabilities": [],
        "provided_capabilities": [],
        "provided_tools": [],
        "published_events": [],
        "consumed_events": [],
        "state_scopes": [],
    }
    payload.update(changes)
    (directory / "manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def make_host(tmp_path, required):
    return PluginRuntimeHost(
        tmp_path,
        "session",
        plugin_dirs=(tmp_path / "plugins",),
        builtin_package=(
            "apps.agent.test.agent_orchestration.plugin_runtime.empty_plugins"
        ),
        required_plugin_ids=frozenset(required),
    )


def test_host按manifest注入能力注册tool并建立事件订阅(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(
        root,
        "producer",
        provided_capabilities=[
            {"capability_id": "sample-api", "version": "1.0.0"}
        ],
        provided_tools=["fixture-tool"],
        published_events=[EVENT],
    )
    write_manifest(
        root,
        "consumer",
        required_capabilities=[
            {
                "plugin_id": "producer",
                "capability_id": "sample-api",
                "version_spec": ">=1,<2",
            }
        ],
        consumed_events=[EVENT],
    )

    async def run():
        host = make_host(tmp_path, {"producer", "consumer"})
        await host.start()
        producer = host.get_plugin("producer")
        consumer = host.get_plugin("consumer")
        await producer.publish(SampleEvent(value="hello"))
        await host.plugin_manager.drain()
        snapshot = host.graph_snapshot
        await host.stop()
        return host, consumer, snapshot

    import asyncio

    host, consumer, snapshot = asyncio.run(run())
    assert [event.value for event in consumer.events] == ["hello"]
    assert snapshot.tools == (("fixture-tool", "producer"),)
    assert snapshot.subscriptions == (("producer", "consumer"),)
    assert host.status == "stopped"


def test_host拒绝plugin发布manifest未声明event(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(
        root,
        "producer",
        provided_capabilities=[
            {"capability_id": "sample-api", "version": "1.0.0"}
        ],
        provided_tools=["fixture-tool"],
    )

    async def run():
        host = make_host(tmp_path, {"producer"})
        await host.start()
        try:
            with pytest.raises(RuntimeError, match="undeclared Event"):
                await host.get_plugin("producer").publish(
                    SampleEvent(value="blocked")
                )
        finally:
            await host.stop()

    import asyncio

    asyncio.run(run())


def test_host可选factory失败被隔离核心factory失败阻止ready(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(root, "optional", fail_factory=True)

    async def optional_run():
        host = make_host(tmp_path, set())
        await host.start()
        assert "optional" not in host.plugin_manager.registry.plugin_ids()
        await host.stop()

    import asyncio

    asyncio.run(optional_run())

    async def required_run():
        host = make_host(tmp_path, {"optional"})
        with pytest.raises(RequiredPluginError, match="optional"):
            await host.start()

    asyncio.run(required_run())


def test_host从配置目录导入外部plugin_factory(tmp_path):
    root = tmp_path / "plugins"
    directory = root / "external-plugin"
    directory.mkdir(parents=True)
    package = directory / "external_plugin"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "factory.py").write_text(
        "from apps.agent.test.agent_orchestration.plugin_runtime.host_fixtures "
        "import FixturePlugin\n"
        "from apps.agent.src.agent_orchestration.plugin_runtime.registration "
        "import PluginRegistration\n"
        "def create_plugin(plugin_id, **kwargs):\n"
        "    return PluginRegistration(plugin=FixturePlugin(plugin_id))\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": 1,
        "plugin_id": "external-plugin",
        "plugin_version": "1.0.0",
        "entrypoint": "external_plugin.factory:create_plugin",
        "python_requires": [],
        "required_capabilities": [],
        "provided_capabilities": [],
        "provided_tools": [],
        "published_events": [],
        "consumed_events": [],
        "state_scopes": [],
    }
    (directory / "manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    async def run():
        host = make_host(tmp_path, {"external-plugin"})
        await host.start()
        plugin = host.get_plugin("external-plugin")
        await host.stop()
        return plugin

    import asyncio

    assert asyncio.run(run()).plugin_id == "external-plugin"


def test_host多个runtime共享外部plugin目录直到最后一个退出(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(root, "shared-plugin")
    directory = str((root / "shared-plugin").resolve())

    async def run():
        first = make_host(tmp_path, {"shared-plugin"})
        second = make_host(tmp_path, {"shared-plugin"})
        await first.start()
        await second.start()
        assert directory in sys.path
        await first.stop()
        assert directory in sys.path
        assert second.get_plugin("shared-plugin").plugin_id == "shared-plugin"
        await second.stop()
        assert directory not in sys.path

    import asyncio

    asyncio.run(run())


def test_host可选plugin声明与factory不一致时清理导入路径(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(
        root,
        "mismatch-plugin",
        provided_tools=["declared-but-missing"],
    )
    directory = str((root / "mismatch-plugin").resolve())

    async def run():
        host = make_host(tmp_path, set())
        await host.start()
        assert "mismatch-plugin" not in host.plugin_manager.registry.plugin_ids()
        assert directory not in sys.path
        await host.stop()

    import asyncio

    asyncio.run(run())


def test_host_eventbus激活失败回滚plugin_tool和外部导入路径(tmp_path):
    root = tmp_path / "plugins"
    write_manifest(
        root,
        "producer",
        provided_capabilities=[
            {"capability_id": "sample-api", "version": "1.0.0"}
        ],
        provided_tools=["fixture-tool"],
    )
    directory = str((root / "producer").resolve())

    async def run():
        host = make_host(tmp_path, {"producer"})

        async def fail_start():
            raise RuntimeError("event bus failed")

        host.plugin_manager.event_bus.start = fail_start
        with pytest.raises(RuntimeError, match="event bus failed"):
            await host.start()
        return host

    import asyncio

    host = asyncio.run(run())
    assert host.plugin_manager.registry.plugin_ids() == []
    assert host.tool_registry.names() == []
    assert host.tool_registry.is_frozen is False
    assert directory not in sys.path
