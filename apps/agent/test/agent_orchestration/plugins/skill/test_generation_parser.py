import json

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.generation_parser import (
    SkillGenerationParseError,
    SkillGenerationParser,
)


def test_parser_accepts_pure_or_single_fenced_json():
    payload = json.dumps({"content": "skill text"})
    parser = SkillGenerationParser()

    assert parser.parse(payload).content == "skill text"
    assert parser.parse(f"```json\n{payload}\n```").content == "skill text"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "prefix {\"content\": \"x\"}",
        "[]",
        "{\"content\": \"x\", \"extra\": true}",
        "{\"content\": \"\"}",
        "```json\n{\"content\": \"x\"}\n``` trailing",
        "{\"content\": NaN}",
    ],
)
def test_parser_rejects_non_exact_output(text):
    with pytest.raises(SkillGenerationParseError):
        SkillGenerationParser().parse(text)
