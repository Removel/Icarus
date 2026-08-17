from datetime import UTC, datetime, timedelta

import pytest

from apps.agent.src.agent_orchestration.plugins.skill import (
    SkillDefinition,
    SkillRanker,
    SkillUsage,
    lifecycle_for_usage,
    normalized_cosine_similarity,
)


NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def make_skill(tmp_path, name):
    return SkillDefinition(
        name=name,
        description=name,
        path=tmp_path / name / "SKILL.md",
        scope="global",
    )


def usage(skill, age_days):
    return SkillUsage(
        workspace_key="workspace",
        skill_key=skill.skill_key,
        discovered_at=NOW - timedelta(days=100),
        last_used_at=NOW - timedelta(days=age_days),
    )


@pytest.mark.parametrize(
    ("days", "status", "score"),
    [
        (0, "active", 1.0),
        (14, "active", 1.0),
        (15, "normal", 0.67),
        (29, "normal", 0.67),
        (30, "archived", 0.33),
        (59, "archived", 0.33),
        (60, "deletion_candidate", 0.0),
    ],
)
def test_lifecycle按UTC日期边界(tmp_path, days, status, score):
    definition = make_skill(tmp_path, "skill")
    assert lifecycle_for_usage(usage(definition, days), NOW) == (status, score)


def test_ranker按80_20得分取top3并稳定排序(tmp_path):
    skills = [make_skill(tmp_path, name) for name in ["d", "b", "a", "c"]]
    usages = {skill.skill_key: usage(skill, 0) for skill in skills}

    ranked = SkillRanker().rank(
        skills,
        query_vector=[1.0, 0.0],
        document_vectors=[
            [-1.0, 0.0],
            [1.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
        ],
        usages=usages,
        now=NOW,
    )

    assert [item.skill.name for item in ranked] == ["a", "b", "c"]
    assert ranked[0].content_score == pytest.approx(1.0)
    assert ranked[0].final_score == pytest.approx(1.0)
    assert ranked[2].content_score == pytest.approx(0.5)
    assert ranked[2].final_score == pytest.approx(0.6)


def test_ranker生命周期参与最终分数且候选不足全返回(tmp_path):
    active = make_skill(tmp_path, "active")
    archived = make_skill(tmp_path, "archived")
    ranked = SkillRanker().rank(
        [archived, active],
        [1.0],
        [[1.0], [1.0]],
        {
            active.skill_key: usage(active, 0),
            archived.skill_key: usage(archived, 30),
        },
        now=NOW,
    )

    assert [item.skill.name for item in ranked] == ["active", "archived"]
    assert ranked[1].final_score == pytest.approx(0.866)


def test_normalized_cosine_similarity归一化到零一():
    assert normalized_cosine_similarity([1, 0], [1, 0]) == pytest.approx(1)
    assert normalized_cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.5)
    assert normalized_cosine_similarity([1, 0], [-1, 0]) == pytest.approx(0)
    assert normalized_cosine_similarity([0, 0], [1, 0]) == pytest.approx(0)
