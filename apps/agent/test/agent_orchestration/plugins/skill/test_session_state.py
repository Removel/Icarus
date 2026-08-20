from apps.agent.src.agent_orchestration.plugins.skill import (
    SessionSkillState,
    SkillDefinition,
)


def skill(tmp_path, name):
    return SkillDefinition(
        name=name,
        description=name,
        path=tmp_path / name / "SKILL.md",
        scope="global",
    )


def test_session_state累计只增且新skill触发full(tmp_path):
    first = skill(tmp_path, "first")
    second = skill(tmp_path, "second")
    state = SessionSkillState()

    initial = state.update([first])
    unchanged = state.update([first])
    extended = state.update([second])

    assert initial.mode == "full"
    assert initial.skills == (first,)
    assert unchanged.mode == "unchanged"
    assert extended.mode == "full"
    assert extended.skills == (first, second)
    assert extended.added == (second,)
    assert state.unchanged_turns == 0


def test_session_state连续第七轮无新增重新full(tmp_path):
    definition = skill(tmp_path, "test")
    state = SessionSkillState()
    state.update([definition])

    updates = [state.update([definition]) for _ in range(7)]

    assert [update.mode for update in updates] == [
        "unchanged",
        "unchanged",
        "unchanged",
        "unchanged",
        "unchanged",
        "unchanged",
        "full",
    ]
    assert state.unchanged_turns == 0


def test_session_state空列表首次仍full后续按周期刷新():
    state = SessionSkillState(refresh_after_unchanged_turns=2)

    assert state.update([]).mode == "full"
    assert state.update([]).mode == "unchanged"
    assert state.update([]).mode == "full"
    assert state.selected_skills == ()


def test_session_state同名skill内容变化时替换并重新full(tmp_path):
    original = SkillDefinition(
        name="same",
        description="old",
        path=tmp_path / "global" / "same" / "SKILL.md",
        scope="global",
    )
    updated = SkillDefinition(
        name="same",
        description="new",
        path=tmp_path / "workspace" / "same" / "SKILL.md",
        scope="workspace",
    )
    state = SessionSkillState()

    state.update([original])
    update = state.update([updated])

    assert update.mode == "full"
    assert update.skills == (updated,)
    assert state.selected_skills == (updated,)
