import math

import pydantic
import pytest

from apps.agent.src.agent_orchestration.plugins.process.config import ProcessConfig


def test_process_config_uses_conservative_defaults():
    config = ProcessConfig()

    assert config.max_running_processes == 8
    assert config.max_terminal_records == 200
    assert config.max_log_bytes == 16 * 1024 * 1024
    assert config.terminate_grace_seconds == 3.0
    assert config.default_log_page_bytes == 32 * 1024
    assert config.max_log_page_bytes == 256 * 1024


@pytest.mark.parametrize(
    "values",
    [
        {"unknown": 1},
        {"max_running_processes": 0},
        {"max_terminal_records": -1},
        {"max_log_bytes": True},
        {"terminate_grace_seconds": math.inf},
        {"default_log_page_bytes": 2, "max_log_page_bytes": 1},
        {"default_page_size": 2, "max_page_size": 1},
    ],
)
def test_process_config_rejects_invalid_values(values):
    with pytest.raises(pydantic.ValidationError):
        ProcessConfig.model_validate(values)
