import json

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.maintenance_parser import (
    SkillMaintenanceParseError,
    SkillMaintenanceParser,
    parse_skill_maintenance_plan,
)


def plan_json(action="no_op"):
    return json.dumps(
        {
            "operations": [
                {
                    "action": action,
                    "reason": "nothing useful to maintain",
                }
            ]
        }
    )


def test_maintenance_parser接受纯json_object():
    plan = SkillMaintenanceParser().parse(plan_json())

    assert plan.operations[0].action == "no_op"


@pytest.mark.parametrize(
    "fence",
    [
        "```json\n{payload}\n```",
        "```JSON  \n{payload}\n```",
        "```\n{payload}\n```",
    ],
)
def test_maintenance_parser接受单个完整fenced_json(fence):
    plan = parse_skill_maintenance_plan(fence.format(payload=plan_json()))

    assert plan.operations[0].reason == "nothing useful to maintain"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "{bad json}",
        "[]",
        "{\"operations\": NaN}",
        "explanation\n" + plan_json(),
        "```json\n" + plan_json() + "\n```\nextra",
        (
            "```json\n"
            + plan_json()
            + "\n```\n```json\n"
            + plan_json()
            + "\n```"
        ),
        "```python\n" + plan_json() + "\n```",
    ],
)
def test_maintenance_parser严格拒绝非单一json文档(text):
    with pytest.raises(SkillMaintenanceParseError):
        SkillMaintenanceParser().parse(text)


def test_maintenance_parser把schema错误包装为领域错误():
    invalid = json.dumps(
        {
            "operations": [
                {
                    "action": "delete",
                    "target_name": "../escape",
                    "reason": "unsafe",
                }
            ]
        }
    )

    with pytest.raises(
        SkillMaintenanceParseError,
        match="required schema",
    ) as error_info:
        SkillMaintenanceParser().parse(invalid)

    assert error_info.value.__cause__ is not None


def test_maintenance_parser拒绝未知字段而不产出可执行计划():
    invalid = json.dumps(
        {
            "operations": [
                {
                    "action": "no_op",
                    "reason": "none",
                    "command": "rm -rf",
                }
            ]
        }
    )

    with pytest.raises(SkillMaintenanceParseError):
        SkillMaintenanceParser().parse(invalid)
