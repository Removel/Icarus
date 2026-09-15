import asyncio
from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.plugins.knowledge.plugin import (
    KnowledgeOperationError,
    KnowledgePlugin,
)

from .helpers import KnowledgeBackendStub


def make_plugin(tmp_path, **limits):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    backend = KnowledgeBackendStub()
    return backend, KnowledgePlugin(
        "knowledge", backend, workspace_path=workspace, **limits
    )


def test_knowledge插件只提供按需操作且不接收事件(tmp_path):
    backend, plugin = make_plugin(tmp_path)
    assert plugin.accepts_event("user-input", object()) is False
    assert plugin.query("question")["answer"] == "answer:question"
    assert plugin.list()["summaries"] == ["summaries/guide"]
    assert plugin.read("summaries/guide")["content"] == "page content"
    asyncio.run(plugin.stop())
    assert backend.closed is True


def test_upload允许workspace内相对和绝对普通文件(tmp_path):
    backend, plugin = make_plugin(tmp_path)
    first = plugin.workspace_path / "first.md"
    second = plugin.workspace_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    output = plugin.upload(("first.md", str(second)))

    assert output["added_count"] == 2
    assert backend.calls[-1] == ("upload", ("first.md", "second.txt"))


@pytest.mark.parametrize(
    "build_path, expected",
    [
        pytest.param(lambda workspace, outside: "../outside.md", "outside", id="dotdot逃逸"),
        pytest.param(lambda workspace, outside: str(outside), "outside", id="绝对路径逃逸"),
        pytest.param(lambda workspace, outside: "folder", "regular file", id="目录"),
        pytest.param(lambda workspace, outside: "script.py", "unsupported", id="不支持格式"),
    ],
)
def test_upload拒绝不安全路径和类型(tmp_path, build_path, expected):
    _, plugin = make_plugin(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (plugin.workspace_path / "folder").mkdir()
    (plugin.workspace_path / "script.py").write_text("x", encoding="utf-8")
    with pytest.raises(KnowledgeOperationError, match=expected):
        plugin.upload((build_path(plugin.workspace_path, outside),))


def test_upload拒绝符号链接逃逸(tmp_path):
    _, plugin = make_plugin(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = plugin.workspace_path / "link.md"
    link.symlink_to(outside)
    with pytest.raises(KnowledgeOperationError, match="outside"):
        plugin.upload(("link.md",))


def test_upload执行单文件和总请求容量边界(tmp_path):
    _, plugin = make_plugin(tmp_path, max_file_bytes=3, max_request_bytes=5)
    (plugin.workspace_path / "large.md").write_bytes(b"1234")
    with pytest.raises(KnowledgeOperationError, match="file exceeds"):
        plugin.upload(("large.md",))
    (plugin.workspace_path / "a.md").write_bytes(b"123")
    (plugin.workspace_path / "b.md").write_bytes(b"123")
    with pytest.raises(KnowledgeOperationError, match="request exceeds"):
        plugin.upload(("a.md", "b.md"))


@pytest.mark.parametrize(
    "document, all_documents",
    [(None, False), ("guide", True)],
)
def test_recompile要求document和all_documents严格二选一(
    tmp_path, document, all_documents
):
    _, plugin = make_plugin(tmp_path)
    with pytest.raises(KnowledgeOperationError, match="exactly one"):
        plugin.recompile(
            document=document, all_documents=all_documents, refresh_schema=False
        )
