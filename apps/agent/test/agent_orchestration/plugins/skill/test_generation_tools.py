import asyncio
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.skill.generation_context import (
    SkillGenerationContext,
    generation_context,
)
from apps.agent.src.agent_orchestration.plugins.skill.generation_tools import (
    GenerationBashTool,
    GenerationCopyTool,
    GenerationReadTool,
    GenerationRemoveTool,
    GenerationWriteTool,
    create_generation_tools,
)
from apps.agent.src.agent_orchestration.plugins.skill.repository import (
    MAX_FILE_BYTES,
)


@pytest.fixture
def active_context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_skills = workspace / "skills"
    draft = workspace_skills / ".drafts" / "draft"
    draft.mkdir(parents=True)
    global_skills = tmp_path / "data" / "skills"
    global_skills.mkdir(parents=True)
    context = SkillGenerationContext(
        draft_dir=draft,
        workspace_dir=workspace,
        global_skills_dir=global_skills,
        workspace_skills_dir=workspace_skills,
    )
    with generation_context(context):
        yield context


def test_generation_tool_names_are_private_minimal_set():
    assert [tool.definition.name for tool in create_generation_tools()] == [
        "read", "write", "copy", "remove", "bash"
    ]


def test_write_read_list_and_remove_stay_in_draft(active_context):
    write = GenerationWriteTool()
    read = GenerationReadTool()
    remove = GenerationRemoveTool()

    result = write.invoke({"path": "scripts/check.py", "content": "print('ok')\n"})
    assert result.success is True
    assert read.invoke({"path": str(active_context.draft_dir / "scripts/check.py")}).output["content"] == "print('ok')\n"
    assert read.invoke({"path": str(active_context.draft_dir / "scripts")}).output["entries"][0]["name"] == "check.py"
    assert remove.invoke({"path": "scripts/check.py"}).success is True
    assert remove.invoke({"path": "scripts"}).success is True


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape"])
def test_write_rejects_paths_outside_draft(active_context, path):
    result = GenerationWriteTool().invoke({"path": path, "content": "x"})
    assert result.success is False


def test_read_allows_workspace_but_rejects_outside_and_credentials(active_context, tmp_path):
    source = active_context.workspace_dir / "source.txt"
    source.write_text("source")
    secret = active_context.workspace_dir / ".env"
    secret.write_text("TOKEN=secret")
    variant_secret = active_context.workspace_dir / ".env.local"
    variant_secret.write_text("TOKEN=secret")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    tool = GenerationReadTool()

    assert tool.invoke({"root": "workspace", "path": "source.txt"}).success is True
    assert tool.invoke({"path": str(outside)}).success is False
    assert tool.invoke({"path": str(secret)}).success is False
    assert tool.invoke({"path": str(variant_secret)}).success is False
    assert tool.invoke({"root": "workspace", "path": "../outside.txt"}).success is False
    assert tool.invoke({"root": "unknown", "path": "source.txt"}).success is False


def test_copy_preserves_binary_and_rejects_outside(active_context, tmp_path):
    source = active_context.workspace_dir / "image.bin"
    source.write_bytes(b"\x00\xffbinary")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    tool = GenerationCopyTool()

    result = tool.invoke(
        {
            "source_root": "workspace",
            "source": "image.bin",
            "path": "assets/image.bin",
        }
    )
    assert result.success is True
    assert (active_context.draft_dir / "assets/image.bin").read_bytes() == b"\x00\xffbinary"
    assert tool.invoke({"source": str(outside), "path": "outside.bin"}).success is False


def test_write_and_copy_reject_oversized_files(active_context):
    oversized = active_context.workspace_dir / "oversized.bin"
    oversized.write_bytes(b"x" * (MAX_FILE_BYTES + 1))

    write_result = GenerationWriteTool().invoke(
        {"path": "large.txt", "content": "x" * (MAX_FILE_BYTES + 1)}
    )
    copy_result = GenerationCopyTool().invoke(
        {"source": str(oversized), "path": "large.bin"}
    )

    assert write_result.success is False
    assert copy_result.success is False
    assert not (active_context.draft_dir / "large.txt").exists()
    assert not (active_context.draft_dir / "large.bin").exists()


def test_write_rejects_symlink_parent(active_context, tmp_path):
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (active_context.draft_dir / "linked").symlink_to(outside, target_is_directory=True)

    result = GenerationWriteTool().invoke(
        {"path": "linked/file.txt", "content": "escape"}
    )

    assert result.success is False
    assert not (outside / "file.txt").exists()


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "pip install thing",
        "python -c 'print(1)'",
        "node --eval 'console.log(1)'",
        "../python check.py",
        "tools/node check.js",
        "python ../outside.py",
        "python script.py > result.txt",
    ],
)
def test_bash_rejects_network_install_inline_and_composed_commands(active_context, command):
    result = GenerationBashTool().invoke({"command": command})
    assert result.success is False


def test_bash_runs_validation_in_draft_with_clean_environment(active_context, monkeypatch):
    (active_context.draft_dir / "check.py").write_text(
        "import os\nassert 'SECRET_TOKEN' not in os.environ\nprint('ok')\n"
    )
    monkeypatch.setenv("SECRET_TOKEN", "should-not-leak")

    result = GenerationBashTool().invoke({"command": "python3 check.py"})

    assert result.success is True
    assert result.output["stdout"] == "ok\n"


def test_bash_timeout_is_bounded(active_context):
    (active_context.draft_dir / "wait.py").write_text(
        "import time\ntime.sleep(5)\n"
    )

    result = GenerationBashTool().invoke(
        {"command": "python3 wait.py", "timeout": 0.01}
    )

    assert result.success is False
    assert "timed out" in result.error


def test_bash_output_is_bounded_while_process_runs(active_context):
    (active_context.draft_dir / "noisy.py").write_text(
        "import sys\nsys.stdout.write('x' * 300000)\n"
    )

    result = GenerationBashTool().invoke({"command": "python3 noisy.py"})

    assert result.success is False
    assert "output exceeded" in result.error


def test_async_bash_cancellation_terminates_process(active_context):
    (active_context.draft_dir / "wait.py").write_text(
        "import time\ntime.sleep(30)\n"
    )

    async def run():
        task = asyncio.create_task(
            GenerationBashTool().ainvoke({"command": "python3 wait.py"})
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
