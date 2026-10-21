"""Authoritative capability definitions, independent of provider identities."""

from typing import ClassVar
from icarus.core.capabilities.conversation import ConversationReadRequest, ConversationReadResponse


class CapabilityContract:
    capability_id: ClassVar[str]
    version: ClassVar[str] = "1.0.0"
    access_mode: ClassVar[str] = "host"
    port_type: ClassVar[type | None] = None
    request_type: ClassVar[type]
    response_type: ClassVar[type]


class ConversationRead(CapabilityContract):
    capability_id = "conversation.read"
    access_mode = "event"
    request_type = ConversationReadRequest
    response_type = ConversationReadResponse


class StateStoreAccess(CapabilityContract):
    capability_id = "state.store"


class TaskControlAccess(CapabilityContract):
    capability_id = "task.control"


class InputSubmitAccess(CapabilityContract):
    capability_id = "input.submit"


class CapabilityCatalog:
    def __init__(self) -> None:
        self._contracts: dict[str, type[CapabilityContract]] = {}

    def register(self, contract: type[CapabilityContract]) -> None:
        if contract.capability_id in self._contracts:
            raise ValueError(f"Duplicate capability contract: {contract.capability_id}")
        self._contracts[contract.capability_id] = contract

    def copy(self) -> "CapabilityCatalog":
        catalog = CapabilityCatalog()
        catalog._contracts.update(self._contracts)
        return catalog

    def resolve(self, capability_id: str) -> type[CapabilityContract]:
        return self._contracts[capability_id]


def builtin_capability_catalog() -> CapabilityCatalog:
    catalog = CapabilityCatalog()
    from icarus.core.registration import PluginStateStore
    from icarus.core.ports import InputSubmission, TaskControl
    StateStoreAccess.port_type = PluginStateStore
    TaskControlAccess.port_type = TaskControl
    InputSubmitAccess.port_type = InputSubmission
    for contract in (ConversationRead, StateStoreAccess, TaskControlAccess, InputSubmitAccess):
        catalog.register(contract)
    return catalog
