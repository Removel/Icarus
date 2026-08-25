import asyncio
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.generation_context import (
    SkillGenerationContext,
    generation_context,
    get_generation_context,
)


def context(tmp_path: Path, name: str) -> SkillGenerationContext:
    workspace = tmp_path / name
    draft = workspace / "skills" / ".drafts" / "draft"
    draft.mkdir(parents=True)
    global_skills = tmp_path / "data" / "skills"
    global_skills.mkdir(parents=True, exist_ok=True)
    return SkillGenerationContext(
        draft_dir=draft,
        workspace_dir=workspace,
        global_skills_dir=global_skills,
        workspace_skills_dir=workspace / "skills",
    )


def test_generation_context_is_required_and_restored(tmp_path):
    current = context(tmp_path, "one")
    with pytest.raises(RuntimeError, match="not active"):
        get_generation_context()

    with generation_context(current):
        assert get_generation_context() is current

    with pytest.raises(RuntimeError, match="not active"):
        get_generation_context()


def test_generation_context_isolated_between_concurrent_tasks(tmp_path):
    async def run():
        contexts = [context(tmp_path, "one"), context(tmp_path, "two")]

        async def inspect(current):
            with generation_context(current):
                await asyncio.sleep(0)
                return get_generation_context().draft_dir

        return await asyncio.gather(*(inspect(item) for item in contexts))

    result = asyncio.run(run())
    assert result[0] != result[1]


def test_context_rejects_workspace_skills_outside_workspace(tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    with pytest.raises(ValueError, match="belong"):
        SkillGenerationContext(
            draft_dir=draft,
            workspace_dir=tmp_path / "workspace",
            global_skills_dir=tmp_path / "global",
            workspace_skills_dir=tmp_path / "other",
        )


def test_context_rejects_draft_outside_configured_roots(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    draft = workspace / "draft"
    draft.mkdir()

    with pytest.raises(ValueError, match="Draft root"):
        SkillGenerationContext(
            draft_dir=draft,
            workspace_dir=workspace,
            global_skills_dir=tmp_path / "global",
            workspace_skills_dir=workspace / "skills",
        )
