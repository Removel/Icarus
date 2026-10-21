import pytest

from apps.agent.test.icarus.runtime.support import SampleEvent


@pytest.fixture
def event_factory():
    return lambda value: SampleEvent(value=value)
