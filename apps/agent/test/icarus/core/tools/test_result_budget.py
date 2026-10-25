import json
from pathlib import Path

import pytest

from icarus.core.tools.result_budget import (
    conservative_token_count,
    preview_json_value,
    render_tool_result,
    serialize_tool_result,
)
from icarus.core.tools.result_store import StoredToolResult
from icarus.core.tools.types import ToolExecutionResult


def test_read_default_character_fragment_progresses_with_3000_visible_budget(tmp_path):
    from agent_app.plugins.builtin_tools import ReadTool
    from icarus.core.tools import ToolExecutor, ToolRegistry
    from icarus.core.models.messages import ToolCall

    path = tmp_path / "long.txt"
    path.write_text("x" * 50000, encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    arguments = {"path": str(path), "_execution": {"max_output_tokens": 3000}}
    result = executor.execute_many([ToolCall("read", "read", arguments)])[0][1]
    assert result.output["content"] == "x" * 2000
    assert result.output["next_text_offset"] == 2000
    assert result.output["has_more"] is False
    assert result.output["truncated"] is True
    assert result.metadata["result_truncated"] is False
    assert conservative_token_count(serialize_tool_result(result)) <= 3000


def test_group_clipping_is_visible_before_following_character_cursor(tmp_path):
    from agent_app.plugins.builtin_tools import ReadTool
    from icarus.core.tools import ToolExecutor, ToolRegistry
    from icarus.core.tools.execution_policy import ToolExecutionPolicy
    from icarus.core.tools.settings import ToolExecutionSettings
    from icarus.core.tools.result_store import ToolResultStore
    from icarus.core.models.messages import ToolCall

    path = tmp_path / "long.txt"
    path.write_text("x" * 50000, encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadTool())
    executor = ToolExecutor(
        registry,
        policy=ToolExecutionPolicy(ToolExecutionSettings(default_output_tokens=3000, batch_output_tokens=3000, max_calls_per_batch=3)),
        result_store=ToolResultStore(tmp_path / "results", max_file_bytes=16 * 1024 * 1024),
    )
    calls = [ToolCall(f"read-{i}", "read", {"path": str(path)}) for i in range(3)]
    results = executor.execute_many(calls, task_id="task-1")
    assert sum(conservative_token_count(serialize_tool_result(r)) for _, r in results) <= 3000
    for _, result in results:
        text = serialize_tool_result(result)
        assert result.metadata["result_truncated"] is True
        assert "truncated" in text
        assert str(result.metadata["result_file"]) in text
        # A renderer preview must not invent a character-consumption cursor.
        if "next_text_offset" in result.output:
            assert result.output["next_text_offset"] == 2000
        raw = json.loads(Path(result.metadata["result_file"]).read_text(encoding="utf-8"))
        assert raw["output"]["content"] == "x" * 2000
        assert "_icarus_omitted" not in raw["output"]
    retried = executor.execute_many([ToolCall("smaller", "read", {"path": str(path), "text_offset": 0, "max_chars": 100})])[0][1]
    assert retried.output["content"] == "x" * 100
    assert retried.output["next_text_offset"] == 100
    assert retried.metadata["result_truncated"] is False


def test_renderer_marks_incomplete_16mib_file_without_full_result_claim(tmp_path):
    from icarus.core.tools.result_store import ToolResultStore

    raw = ToolExecutionResult(True, "x" * (16 * 1024 * 1024 + 1))
    stored = ToolResultStore(tmp_path / "results", max_file_bytes=16 * 1024 * 1024).save(
        task_id="task-1", tool_call_id="large", content=serialize_tool_result(raw, pretty=True)
    )
    rendered = render_tool_result(raw, max_tokens=1000, preview_tokens=900, stored=stored)
    text = serialize_tool_result(rendered)
    assert stored.complete is False
    assert rendered.metadata["result_file_complete"] is False
    assert "incomplete Tool Result file" in text
    assert "full Tool Result:" not in text
    assert conservative_token_count(text) <= 1000



@pytest.mark.parametrize("budget", [512, 1000])
def test_failure_view_preserves_exit_stdout_and_final_diagnostic_at_small_budgets(budget):
    stored = StoredToolResult(Path("/tmp/tool-results/task-1/failed-1234567890123456.txt"), 100000, 100000, True)
    diagnostic = "frame\n" * 1000 + "ValueError: final evidence"
    rendered = render_tool_result(
        ToolExecutionResult(False, {"exit_code": 7, "stdout": "created file\n" + "progress\n" * 1000, "stderr": diagnostic}, diagnostic),
        max_tokens=budget, preview_tokens=int(budget * 0.9), stored=stored,
    )
    assert rendered.output["exit_code"] == 7
    assert "created file" in rendered.output["stdout"]
    assert "ValueError: final evidence" in rendered.error
    assert str(stored.path) in serialize_tool_result(rendered)
    assert conservative_token_count(serialize_tool_result(rendered)) <= budget


def test_short_failure_diagnostic_is_not_repeated_when_stdout_needs_preview():
    stored = StoredToolResult(Path("/tmp/tool-results/task-1/failed.txt"), 10000, 10000, True)
    rendered = render_tool_result(
        ToolExecutionResult(False, {"exit_code": 7, "stdout": "data" * 2000, "stderr": "short diagnostic"}, "short diagnostic"),
        max_tokens=1000, preview_tokens=900, stored=stored,
    )
    assert rendered.error == "short diagnostic"
    assert serialize_tool_result(rendered).count("short diagnostic") == 1


def test_short_multiline_error_keeps_context_when_stdout_is_large():
    stored = StoredToolResult(Path("/tmp/tool-results/task-1/failed.txt"), 10000, 10000, True)
    diagnostic = "write failed after creating file\npermission denied"
    rendered = render_tool_result(
        ToolExecutionResult(False, {"exit_code": 7, "stdout": "data" * 2000}, diagnostic),
        max_tokens=1000, preview_tokens=900, stored=stored,
    )
    assert rendered.error == diagnostic


@pytest.mark.parametrize("budget", [512, 1000, 3000])
def test_wide_failure_mapping_preserves_concise_error_exit_and_stdout(budget):
    stored = StoredToolResult(Path("/tmp/results/failure.txt"), 10000, 10000, True)
    original = ToolExecutionResult(
        False,
        {
            "exit_code": 7,
            "stdout": "created file before failure\n",
            **{f"k{i}": i for i in range(100)},
            "stderr": "permission denied",
        },
        "permission denied",
    )
    rendered = render_tool_result(
        original, max_tokens=budget, preview_tokens=int(budget * 0.9), stored=stored
    )
    assert rendered.success is False
    assert rendered.error == "permission denied"
    assert rendered.output["exit_code"] == 7
    assert rendered.output["stdout"] == "created file before failure\n"
    text = serialize_tool_result(rendered)
    assert str(stored.path) in text
    assert "_icarus_omitted" in text
    assert conservative_token_count(text) <= min(budget, 1000)


def test_result_budget未超限保持原结果():
    result = ToolExecutionResult(True, {"items": [1, 2, 3]})
    rendered = render_tool_result(
        result, max_tokens=1000, preview_tokens=900
    )
    assert rendered.output == result.output
    assert rendered.metadata["result_truncated"] is False


def test_result_budget长字符串保留头尾并携带文件路径(tmp_path):
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = ToolExecutionResult(True, "A" * 500 + "M" * 500 + "Z" * 500)

    rendered = render_tool_result(
        result, max_tokens=400, preview_tokens=300, stored=stored
    )

    text = rendered.output
    assert isinstance(text, str)
    assert text.startswith("A") and text.endswith("Z")
    assert str(stored.path) in text
    assert conservative_token_count(serialize_tool_result(rendered)) <= 400


def test_result_budget大量小元素累计超限并保持合法json(tmp_path):
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = ToolExecutionResult(
        True, {"items": [{"id": index} for index in range(500)]}
    )

    rendered = render_tool_result(
        result, max_tokens=600, preview_tokens=500, stored=stored
    )

    encoded = serialize_tool_result(rendered)
    json.loads(encoded)
    assert conservative_token_count(encoded) <= 600
    assert "_icarus_omitted" in encoded
    assert '"id": 0' in encoded
    assert '"id": 499' in encoded


def test_result_budget占位字段自动避让(tmp_path):
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = ToolExecutionResult(
        True,
        {"_icarus_omitted": "business", **{f"k{i}": "v" * 20 for i in range(100)}},
    )
    rendered = render_tool_result(
        result, max_tokens=500, preview_tokens=400, stored=stored
    )
    encoded = serialize_tool_result(rendered)
    assert "_icarus_omitted_2" in encoded
    assert conservative_token_count(encoded) <= 500


def test_result_budget_tokenizer失败时回退到保守计量():
    def broken_counter(text):
        del text
        raise RuntimeError("tokenizer unavailable")

    rendered = render_tool_result(
        ToolExecutionResult(True, "x" * 2000),
        max_tokens=300,
        preview_tokens=250,
        counter=broken_counter,
    )

    assert rendered.metadata["result_truncated"] is True
    assert conservative_token_count(serialize_tool_result(rendered)) <= 300


def test_result_budget非json对象按实际字符串序列化后降级():
    class LargeValue:
        def __str__(self):
            return "value-" * 1000

    rendered = render_tool_result(
        ToolExecutionResult(True, LargeValue()),
        max_tokens=300,
        preview_tokens=250,
    )

    assert isinstance(rendered.output, str)
    assert "content omitted" in rendered.output
    assert conservative_token_count(serialize_tool_result(rendered)) <= 300


def test_preview_json_value使用无路径省略标记并返回截断状态():
    preview, truncated = preview_json_value(
        {"items": [f"item-{index}" for index in range(500)]},
        max_tokens=300,
        omission_marker="more output omitted",
    )
    encoded = json.dumps(
        preview, ensure_ascii=False, separators=(",", ":")
    )

    assert truncated is True
    assert "more output omitted" in encoded
    assert "path" not in encoded
    assert conservative_token_count(encoded) <= 300
