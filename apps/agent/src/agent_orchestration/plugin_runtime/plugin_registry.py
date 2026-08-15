"""Plugin 注册与来源订阅关系。"""

from apps.agent.src.agent_orchestration.plugin_runtime.base_plugin import BasePlugin
from apps.agent.src.agent_orchestration.plugin_runtime.types import (
    PluginId,
    PluginStatus,
    Subscription,
)


class PluginRegistry:
    """只按来源 Plugin 维护订阅关系。"""

    def __init__(self) -> None:
        self._plugins: dict[PluginId, BasePlugin] = {}
        self._statuses: dict[PluginId, PluginStatus] = {}
        self._subscriptions: dict[str, Subscription] = {}
        self._source_subscriptions: dict[PluginId, list[str]] = {}
        self._subscription_keys: dict[tuple[PluginId, PluginId], str] = {}

    def register(self, plugin: BasePlugin) -> None:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"Plugin is already registered: {plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin
        self._statuses[plugin.plugin_id] = PluginStatus.CREATED

    def unregister(self, plugin_id: PluginId) -> BasePlugin:
        plugin = self.get(plugin_id)
        status = self.get_status(plugin_id)
        if status not in {PluginStatus.CREATED, PluginStatus.STOPPED}:
            raise RuntimeError(
                f"Running plugin cannot be unregistered: {plugin_id} status={status.value}"
            )

        related = [
            subscription_id
            for subscription_id, subscription in self._subscriptions.items()
            if subscription.source_plugin_id == plugin_id
            or subscription.subscriber_plugin_id == plugin_id
        ]
        for subscription_id in related:
            self.unsubscribe(subscription_id)

        del self._plugins[plugin_id]
        del self._statuses[plugin_id]
        return plugin

    def get(self, plugin_id: PluginId) -> BasePlugin:
        try:
            return self._plugins[plugin_id]
        except KeyError as error:
            raise KeyError(f"Plugin is not registered: {plugin_id}") from error

    def contains(self, plugin_id: PluginId) -> bool:
        return plugin_id in self._plugins

    def plugin_ids(self) -> list[PluginId]:
        return list(self._plugins)

    def get_status(self, plugin_id: PluginId) -> PluginStatus:
        self.get(plugin_id)
        return self._statuses[plugin_id]

    def set_status(self, plugin_id: PluginId, status: PluginStatus) -> None:
        self.get(plugin_id)
        self._statuses[plugin_id] = status

    def subscribe(
        self,
        subscriber_plugin_id: PluginId,
        source_plugin_id: PluginId,
    ) -> Subscription:
        self.get(source_plugin_id)
        self.get(subscriber_plugin_id)
        key = (source_plugin_id, subscriber_plugin_id)
        existing_id = self._subscription_keys.get(key)
        if existing_id is not None:
            return self._subscriptions[existing_id]

        subscription = Subscription(
            source_plugin_id=source_plugin_id,
            subscriber_plugin_id=subscriber_plugin_id,
        )
        self._subscriptions[subscription.subscription_id] = subscription
        self._subscription_keys[key] = subscription.subscription_id
        self._source_subscriptions.setdefault(source_plugin_id, []).append(
            subscription.subscription_id
        )
        return subscription

    def unsubscribe(self, subscription_id: str) -> Subscription:
        try:
            subscription = self._subscriptions.pop(subscription_id)
        except KeyError as error:
            raise KeyError(
                f"Subscription is not registered: {subscription_id}"
            ) from error

        key = (
            subscription.source_plugin_id,
            subscription.subscriber_plugin_id,
        )
        self._subscription_keys.pop(key, None)
        source_subscriptions = self._source_subscriptions.get(
            subscription.source_plugin_id,
            [],
        )
        source_subscriptions.remove(subscription_id)
        if not source_subscriptions:
            self._source_subscriptions.pop(subscription.source_plugin_id, None)
        return subscription

    def get_subscriber_ids(self, source_plugin_id: PluginId) -> list[PluginId]:
        self.get(source_plugin_id)
        return [
            self._subscriptions[subscription_id].subscriber_plugin_id
            for subscription_id in self._source_subscriptions.get(
                source_plugin_id,
                [],
            )
        ]
