import pytest

from apps.agent.test.agent_orchestration.plugin_runtime.support import SampleEvent


@pytest.fixture
def event_factory():
    return lambda value: SampleEvent(value=value)
