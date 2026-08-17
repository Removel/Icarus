from datetime import UTC, datetime, timedelta

from apps.agent.src.agent_orchestration.plugins.skill import (
    SkillDefinition,
    SkillUsageStore,
)


def skill(tmp_path, name="test"):
    return SkillDefinition(
        name=name,
        description=f"description {name}",
        path=tmp_path / name / "SKILL.md",
        scope="global",
    )


def test_usage_store首次发现不覆盖时间且命中递增(tmp_path):
    definition = skill(tmp_path)
    first_seen = datetime(2026, 1, 1, tzinfo=UTC)
    used_at = first_seen + timedelta(days=3)
    with SkillUsageStore(tmp_path / "skill-state.sqlite3") as store:
        initial = store.ensure_discovered(
            "workspace-a", [definition], now=first_seen
        )[definition.skill_key]
        store.ensure_discovered(
            "workspace-a", [definition], now=used_at
        )
        first_use = store.mark_used(
            "workspace-a", [definition], now=used_at
        )[definition.skill_key]
        second_use = store.mark_used(
            "workspace-a", [definition], now=used_at + timedelta(hours=1)
        )[definition.skill_key]

    assert initial.discovered_at == first_seen
    assert initial.last_used_at is None
    assert first_use.discovered_at == first_seen
    assert first_use.use_count == 1
    assert second_use.use_count == 2
    assert second_use.last_used_at == used_at + timedelta(hours=1)


def test_usage_store按workspace隔离并支持空批次(tmp_path):
    definition = skill(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with SkillUsageStore(tmp_path / "skill-state.sqlite3") as store:
        store.ensure_discovered("workspace-a", [definition], now=now)
        store.mark_used("workspace-a", [definition], now=now)
        workspace_b = store.ensure_discovered(
            "workspace-b", [definition], now=now
        )[definition.skill_key]
        assert store.ensure_discovered("workspace-a", [], now=now) == {}
        assert store.mark_used("workspace-a", [], now=now) == {}

    assert workspace_b.use_count == 0
    assert workspace_b.last_used_at is None


def test_usage_store关闭幂等且收紧文件权限(tmp_path):
    database = tmp_path / "skills" / "skill-state.sqlite3"
    store = SkillUsageStore(database)

    store.close()
    store.close()

    assert database.stat().st_mode & 0o777 == 0o600
    assert database.parent.stat().st_mode & 0o777 == 0o700
