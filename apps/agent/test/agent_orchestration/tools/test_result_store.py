from pathlib import Path

import pytest

from apps.agent.src.agent_orchestration.tools.result_store import ToolResultStore


def test_tool_result_store原子保存session文本并限制权限(tmp_path):
    store = ToolResultStore(tmp_path / "tool-results", max_file_bytes=1024)

    result = store.save(
        task_id="task-1", tool_call_id="call/unsafe", content="hello 世界"
    )

    assert result.path.read_text(encoding="utf-8") == "hello 世界"
    assert result.path.parent == tmp_path / "tool-results" / "task-1"
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert result.path.parent.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "tool-results").stat().st_mode & 0o777 == 0o700
    assert result.complete is True


def test_tool_result_store超过单文件上限只保存受限字节(tmp_path):
    store = ToolResultStore(tmp_path / "tool-results", max_file_bytes=4)

    result = store.save(task_id="task", tool_call_id="call", content="abcdef")

    assert result.path.read_bytes() == b"abcd"
    assert result.original_bytes == 6
    assert result.saved_bytes == 4
    assert result.complete is False


def test_tool_result_store截断时不写入半个utf8字符(tmp_path):
    store = ToolResultStore(tmp_path / "tool-results", max_file_bytes=4)

    result = store.save(
        task_id="task", tool_call_id="call", content="你你"
    )

    assert result.path.read_text(encoding="utf-8") == "你"
    assert result.saved_bytes == 3
    assert result.complete is False


def test_tool_result_store保留上游采集不完整状态(tmp_path):
    store = ToolResultStore(tmp_path / "tool-results", max_file_bytes=1024)

    result = store.save(
        task_id="task",
        tool_call_id="call",
        content="partial",
        source_complete=False,
    )

    assert result.path.read_text(encoding="utf-8") == "partial"
    assert result.complete is False


@pytest.mark.parametrize("task_id", ["", "../escape", "a/b"])
def test_tool_result_store拒绝不安全task_id(tmp_path, task_id):
    store = ToolResultStore(tmp_path, max_file_bytes=10)
    with pytest.raises(ValueError):
        store.save(task_id=task_id, tool_call_id="call", content="x")
