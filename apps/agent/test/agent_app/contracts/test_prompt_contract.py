import importlib
from dataclasses import FrozenInstanceError

import pytest

from icarus.core.capabilities import CapabilityStatus
from icarus.core.capabilities.requester import CapabilityRequester


def contracts():
    try:
        return importlib.import_module("agent_app.contracts.prompt")
    except ModuleNotFoundError:
        pytest.fail("Prompt contracts are not available")


def test_prompt_query_can_return_framework_failures_without_success_payload():
    module = contracts()
    request = module.PromptReadRequest(reason="start", task_id="task")
    for status in (CapabilityStatus.TIMEOUT, CapabilityStatus.UNAVAILABLE, CapabilityStatus.FAILED):
        response = CapabilityRequester.failure(request, status)
        assert response.system_prompt == ""
        assert response.status is status
        assert response.task_id == "task"
        assert response.correlation_id == request.correlation_id


def test_prompt_contract_exports_bind_events_and_capability():
    module = contracts()
    assert module.EVENTS == {
        "prompt.contribution": module.PromptContributionEvent,
        "prompt.update": module.PromptUpdateEvent,
    }
    assert module.CAPABILITIES == (module.PromptRead,)
    assert module.PromptRead.request_type is module.PromptReadRequest
    assert module.PromptRead.response_type is module.PromptReadResponse
    section = module.PromptSection("personality", "persona", "warm")
    with pytest.raises(FrozenInstanceError):
        section.content = "changed"


@pytest.mark.parametrize("section_id,layer,content", [
    ("", "persona", "x"), ("bad id", "persona", "x"),
    ("p", "unknown", "x"), ("p", "persona", None),
])
def test_prompt_sections_reject_invalid_values(section_id, layer, content):
    module = contracts()
    with pytest.raises((ValueError, TypeError)):
        module.PromptSection(section_id, layer, content)


def test_session_layer_is_static_and_ordered_with_other_system_layers():
    module = contracts()
    assert "session" in module.STATIC_LAYERS
    assert module.STATIC_LAYERS.index("workspace") < module.STATIC_LAYERS.index("session")
    assert module.STATIC_LAYERS.index("session") < module.STATIC_LAYERS.index("persona")
    assert module.STATIC_LAYERS[-1] == "external"
    assert module.PromptSection("guide", "session", "stable guidance")


def test_contributor_only_requires_static_initialization():
    module = contracts()

    class Contributor:
        def static_prompt_sections(self):
            return (module.PromptSection("persona", "persona", "warm"),)

    assert isinstance(Contributor(), module.PromptContributor)


def test_contribution_requires_task_and_valid_terminal_status():
    module = contracts()
    with pytest.raises(ValueError):
        module.PromptContributionEvent(status="completed")
    with pytest.raises(ValueError):
        module.PromptContributionEvent(task_id="task", status="pending")
    with pytest.raises(ValueError):
        module.PromptReadRequest(reason="reload")
