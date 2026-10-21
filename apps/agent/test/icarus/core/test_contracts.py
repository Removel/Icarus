"""Cross-plugin event contracts resolve without importing any plugin implementation."""

import importlib


def test_shared_context_contracts_are_framework_owned():
    context = importlib.import_module("icarus.core.contracts.context")
    inputs = importlib.import_module("icarus.core.contracts.input")
    process = importlib.import_module("icarus.core.contracts.process")
    assert context.BlackboardRegionUpdatedEvent.__module__ == "icarus.core.contracts.context"
    assert inputs.InputAccepted("task", 0).queue_position == 0
    assert process.ProcessUpdatedEvent.__module__ == "icarus.core.contracts.process"
