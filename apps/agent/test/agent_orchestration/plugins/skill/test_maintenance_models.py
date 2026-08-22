from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from apps.agent.src.agent_orchestration.plugins.skill.maintenance_models import (
    SkillMaintenanceOperation,
    SkillMaintenancePlan,
    is_safe_skill_name,
)


SKILL_CONTENT = "---\nname: reusable-skill\ndescription: reusable\n---\nbody\n"


def operation(action, **overrides):
    values = {
        "action": action,
        "target_name": f"{action}-skill",
        "reason": f"reason for {action}",
    }
    if action in {"create", "update", "merge"}:
        values["content"] = SKILL_CONTENT
    if action == "merge":
        values["source_names"] = ["source-one", "source-two"]
    if action == "no_op":
        values.pop("target_name")
    values.update(overrides)
    return SkillMaintenanceOperation(**values)


@pytest.mark.parametrize(
    "action",
    ["create", "update", "merge", "delete", "no_op"],
)
def test_maintenance_operation支持全部合法action(action):
    item = operation(action)

    assert item.action == action
    assert item.reason == f"reason for {action}"


def test_maintenance_content只做结构校验不提前解析yaml():
    item = operation("create", content="complete text validated later")

    assert item.content == "complete text validated later"


@pytest.mark.parametrize("action", ["create", "update", "merge"])
def test_maintenance写操作必须提供非空完整content(action):
    with pytest.raises(ValidationError, match="requires complete non-blank"):
        operation(action, content="  ")


def test_maintenance_merge至少两个不同安全来源():
    with pytest.raises(ValidationError, match="at least two source_names"):
        operation("merge", source_names=["only-one"])

    with pytest.raises(ValidationError, match="distinct source_names"):
        operation("merge", source_names=["same", "same"])


def test_maintenance_delete拒绝content():
    with pytest.raises(ValidationError, match="delete cannot include content"):
        operation("delete", content=SKILL_CONTENT)


@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_maintenance非merge操作拒绝source_names(action):
    with pytest.raises(ValidationError, match="cannot include source_names"):
        operation(action, source_names=["source-one"])


def test_maintenance_no_op不接受操作字段且必须是计划唯一项():
    with pytest.raises(ValidationError, match="no_op cannot include"):
        operation("no_op", target_name="unexpected")

    with pytest.raises(ValidationError, match="no_op must be the only"):
        SkillMaintenancePlan(
            operations=[operation("no_op"), operation("delete")]
        )

    plan = SkillMaintenancePlan(operations=[operation("no_op")])
    assert plan.operations[0].action == "no_op"


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "nested/skill",
        "UpperCase",
        "white space",
        ".hidden",
        "-leading",
        "a" * 65,
        "",
    ],
)
def test_maintenance拒绝不安全或未规范化名称(name):
    assert is_safe_skill_name(name) is False
    with pytest.raises(ValidationError, match="normalized safe Skill name"):
        operation("delete", target_name=name)


def test_maintenance来源名称同样必须安全():
    with pytest.raises(ValidationError, match="source_names"):
        operation("merge", source_names=["safe", "../unsafe"])


def test_maintenance_plan拒绝重复目标并限制一到十项():
    with pytest.raises(ValidationError, match="same Skill"):
        SkillMaintenancePlan(
            operations=[
                operation("create", target_name="same"),
                operation("delete", target_name="same"),
            ]
        )

    ten = SkillMaintenancePlan(
        operations=[
            operation("delete", target_name=f"skill-{index}")
            for index in range(10)
        ]
    )
    assert len(ten.operations) == 10

    with pytest.raises(ValidationError):
        SkillMaintenancePlan(operations=[])
    with pytest.raises(ValidationError):
        SkillMaintenancePlan(
            operations=[
                operation("delete", target_name=f"skill-{index}")
                for index in range(11)
            ]
        )


@pytest.mark.parametrize(
    "operations",
    [
        [
            operation(
                "merge",
                target_name="combined",
                source_names=["source-a", "source-b"],
            ),
            operation("update", target_name="source-a"),
        ],
        [
            operation(
                "merge",
                target_name="first-combined",
                source_names=["source-a", "source-b"],
            ),
            operation(
                "merge",
                target_name="second-combined",
                source_names=["source-b", "source-c"],
            ),
        ],
    ],
)
def test_maintenance_plan拒绝merge来源被其他操作重复触及(operations):
    with pytest.raises(ValidationError, match="same Skill"):
        SkillMaintenancePlan(operations=operations)


def test_maintenance_merge允许把一个来源作为自身目标():
    plan = SkillMaintenancePlan(
        operations=[
            operation(
                "merge",
                target_name="source-a",
                source_names=["source-a", "source-b"],
            )
        ]
    )

    assert plan.operations[0].target_name == "source-a"


def test_maintenance_models拒绝未知字段且不可变():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SkillMaintenanceOperation(
            action="delete",
            target_name="old-skill",
            reason="old",
            invented=True,
        )

    item = operation("delete")
    with pytest.raises((ValidationError, FrozenInstanceError)):
        item.reason = "changed"
