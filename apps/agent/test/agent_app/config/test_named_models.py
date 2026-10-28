"""Named model roles and configuration path selection."""

import pytest
from pydantic import ValidationError

from agent_app.config import ConfigModel


def model(name):
    return {"name": name, "model_name": "test", "context_window": 1000,
            "max_tokens": 100, "temperature": 0, "default_think_level": "low"}


def config(models, **changes):
    return ConfigModel(
        openai_base_url="https://example.com", anthropic_base_url="https://example.com",
        model_settings=models, **changes,
    )


def test_models_can_be_selected_by_arbitrary_unique_name():
    value = config([model("worker"), model("summary")], model_roles={
        "agent": "worker", "compaction": "summary", "skill_generation": "worker", "title": "summary",
    })
    assert value.get_model("worker").model_name == "test"
    assert value.model_roles.title == "summary"
    assert isinstance(value.model_settings, list)


@pytest.mark.parametrize("models", [[model("worker"), model("worker")], [model(" ")]])
def test_model_names_must_be_nonempty_and_unique(models):
    with pytest.raises(ValidationError):
        config(models)


def test_unknown_role_reference_fails_during_configuration_load():
    with pytest.raises(ValidationError, match="role"):
        config([model("worker")])
