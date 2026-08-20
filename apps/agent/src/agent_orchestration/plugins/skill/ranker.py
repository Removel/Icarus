"""Rank Skills by semantic similarity and per-Workspace lifecycle."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import numpy as np

from apps.agent.src.agent_orchestration.plugins.skill.models import (
    LifecycleStatus,
    RankedSkill,
    SkillDefinition,
    SkillUsage,
)


_LIFECYCLE_SCORES: dict[LifecycleStatus, float] = {
    "active": 1.0,
    "normal": 0.67,
    "archived": 0.33,
    "deletion_candidate": 0.0,
}


class SkillRanker:
    def __init__(
        self,
        *,
        content_weight: float = 0.8,
        lifecycle_weight: float = 0.2,
        limit: int = 3,
        minimum_content_score: float = 0.8,
    ) -> None:
        if content_weight < 0 or lifecycle_weight < 0:
            raise ValueError("Ranking weights cannot be negative")
        if not np.isclose(content_weight + lifecycle_weight, 1.0):
            raise ValueError("Ranking weights must sum to 1")
        if limit < 1:
            raise ValueError("Ranking limit must be positive")
        if not 0 <= minimum_content_score <= 1:
            raise ValueError("Minimum content score must be between 0 and 1")
        self.content_weight = content_weight
        self.lifecycle_weight = lifecycle_weight
        self.limit = limit
        self.minimum_content_score = minimum_content_score

    def rank(
        self,
        skills: Sequence[SkillDefinition],
        query_vector: Sequence[float],
        document_vectors: Sequence[Sequence[float]],
        usages: Mapping[str, SkillUsage],
        *,
        now: datetime | None = None,
    ) -> list[RankedSkill]:
        ranked, _ = self.rank_with_summary(
            skills,
            query_vector,
            document_vectors,
            usages,
            now=now,
        )
        return ranked

    def rank_with_summary(
        self,
        skills: Sequence[SkillDefinition],
        query_vector: Sequence[float],
        document_vectors: Sequence[Sequence[float]],
        usages: Mapping[str, SkillUsage],
        *,
        now: datetime | None = None,
    ) -> tuple[list[RankedSkill], int]:
        if len(skills) != len(document_vectors):
            raise ValueError("Each Skill requires one document vector")
        ranking_time = _require_aware(now or datetime.now(UTC))
        ranked: list[RankedSkill] = []
        for skill, document_vector in zip(skills, document_vectors, strict=True):
            content_score = normalized_cosine_similarity(
                query_vector,
                document_vector,
            )
            if content_score < self.minimum_content_score:
                continue
            usage = usages.get(skill.skill_key)
            status, lifecycle_score = lifecycle_for_usage(usage, ranking_time)
            final_score = (
                content_score * self.content_weight
                + lifecycle_score * self.lifecycle_weight
            )
            ranked.append(
                RankedSkill(
                    skill=skill,
                    content_score=content_score,
                    lifecycle_status=status,
                    lifecycle_score=lifecycle_score,
                    final_score=final_score,
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.final_score,
                item.skill.normalized_name,
                str(item.skill.path),
            )
        )
        return ranked[: self.limit], len(ranked)


def normalized_cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if left_array.ndim != 1 or right_array.ndim != 1:
        raise ValueError("Embedding vectors must be one-dimensional")
    if left_array.shape != right_array.shape or left_array.size == 0:
        raise ValueError("Embedding vectors must have the same non-zero shape")
    denominator = np.linalg.norm(left_array) * np.linalg.norm(right_array)
    if denominator == 0:
        return 0.0
    cosine = float(np.dot(left_array, right_array) / denominator)
    return float(np.clip((cosine + 1.0) / 2.0, 0.0, 1.0))


def lifecycle_for_usage(
    usage: SkillUsage | None,
    now: datetime,
) -> tuple[LifecycleStatus, float]:
    now_utc = _require_aware(now)
    reference = usage.last_used_at or usage.discovered_at if usage else now_utc
    reference_utc = _require_aware(reference)
    unused_days = max(0, (now_utc.date() - reference_utc.date()).days)
    if unused_days <= 14:
        status: LifecycleStatus = "active"
    elif unused_days <= 29:
        status = "normal"
    elif unused_days <= 59:
        status = "archived"
    else:
        status = "deletion_candidate"
    return status, _LIFECYCLE_SCORES[status]


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Lifecycle timestamps must be timezone-aware")
    return value.astimezone(UTC)
