"""Caller-owned catalogs are isolated from host-local extension registration."""

from dataclasses import dataclass

import pytest
from icarus.core.events import Event
from icarus.core.capabilities.catalog import CapabilityCatalog, CapabilityContract
from icarus.core.events.catalog import EventCatalog


@dataclass(frozen=True, kw_only=True)
class Notice(Event):
    pass


class Access(CapabilityContract):
    capability_id = "test.access"


def test_copied_catalog_retains_class_identity_without_sharing_registrations():
    events = EventCatalog()
    events.register("test.notice", Notice)
    extended = events.copy()
    assert extended.resolve("test.notice") is Notice
    class Later(Event):
        pass
    extended.register("test.later", Later)
    with pytest.raises(KeyError):
        events.resolve("test.later")
    capabilities = CapabilityCatalog()
    capabilities.register(Access)
    extended_capabilities = capabilities.copy()
    assert extended_capabilities.resolve("test.access") is Access
    class LaterAccess(CapabilityContract):
        capability_id = "test.later"
    extended_capabilities.register(LaterAccess)
    with pytest.raises(KeyError):
        capabilities.resolve("test.later")
