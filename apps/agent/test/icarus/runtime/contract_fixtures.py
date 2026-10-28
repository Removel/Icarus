"""External plugin fixtures with their own event and request/response definitions."""

import json
from pathlib import Path


CONTRACTS = '''from dataclasses import dataclass
from icarus.core.events import Event
from icarus.core.capabilities import CapabilityRequest, CapabilityResponse
from icarus.core.capabilities.catalog import CapabilityContract

@dataclass(frozen=True, kw_only=True)
class Changed(Event):
    value: str

@dataclass(frozen=True, kw_only=True)
class Reply(CapabilityResponse):
    value: str = ""

@dataclass(frozen=True, kw_only=True)
class Query(CapabilityRequest):
    capability_id = "extension.lookup"
    response_type = Reply

class Lookup(CapabilityContract):
    capability_id = "extension.lookup"
    access_mode = "event"
    request_type = Query
    response_type = Reply

EVENTS = {"extension.changed": Changed}
CAPABILITIES = (Lookup,)
'''

PROVIDER = '''from .contracts import Changed, Lookup, Query, Reply
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration, ProvidedCapability
from icarus.core.capabilities import CapabilityStatus

class Provider(BasePlugin):
    provided_capabilities = (Lookup,)
    @handles(Query)
    async def lookup(self, source, event):
        return (Reply(task_id=event.task_id, correlation_id=event.correlation_id,
                      status=CapabilityStatus.OK, value="provider-answer"),)

def create_plugin(plugin_id, **kwargs):
    plugin = Provider(plugin_id)
    return PluginRegistration(plugin, capabilities=(ProvidedCapability("extension.lookup", "1.0.0", plugin),))
'''

CONSUMER = '''import asyncio
from demo_provider.contracts import Changed, Query
from icarus.core.plugin import BasePlugin, handles
from icarus.core.registration import PluginRegistration

class Consumer(BasePlugin):
    def __init__(self, plugin_id):
        super().__init__(plugin_id)
        self.responses = []
        self.finished = asyncio.Event()
    @handles(Changed)
    async def changed(self, source, event):
        response = (await self.request(Query(task_id=event.task_id))).unwrap()
        self.responses.append((source, event.value, response.value))
        self.finished.set()

def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(Consumer(plugin_id))
'''

EMPTY = '''from icarus.core.plugin import BasePlugin
from icarus.core.registration import PluginRegistration

def create_plugin(plugin_id, **kwargs):
    return PluginRegistration(BasePlugin(plugin_id))
'''


def write_plugin(root: Path, plugin_id: str, *, factory: str = EMPTY,
                 contract_source: str | None = None, **changes) -> Path:
    directory = root / plugin_id
    package_name = plugin_id.replace("-", "_")
    package = directory / package_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "factory.py").write_text(factory)
    if contract_source is not None:
        (package / "contracts.py").write_text(contract_source)
    manifest = {
        "schema_version": 2, "plugin_id": plugin_id, "plugin_version": "1.0.0",
        "entrypoint": f"{package_name}.factory:create_plugin", "tier": "external",
        "python_requires": [], "required_capabilities": [], "provided_capabilities": [],
        "provided_tools": [], "published_events": [], "consumed_events": [], "state_scopes": [],
    }
    manifest.update(changes)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return package
