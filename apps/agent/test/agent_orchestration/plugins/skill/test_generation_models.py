import pytest
from pydantic import ValidationError

from apps.agent.src.agent_orchestration.plugins.skill.generation_models import (
    GeneratedSkill,
)


def test_generated_skill_accepts_one_non_empty_content_field():
    result = GeneratedSkill.model_validate({"content": "---\nname: x\n---\n"})
    assert result.content.startswith("---")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": "   "},
        {"content": 1},
        {"content": "ok", "extra": True},
    ],
)
def test_generated_skill_rejects_invalid_shape(payload):
    with pytest.raises(ValidationError):
        GeneratedSkill.model_validate(payload)
