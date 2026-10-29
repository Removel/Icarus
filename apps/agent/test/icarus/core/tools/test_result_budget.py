import json

import pytest

from icarus.core.tools.result_budget import (
    conservative_token_count,
    preview_json_value,
    render_tool_result,
    serialize_tool_result,
)
from icarus.core.tools.result_store import StoredToolResult
from icarus.core.tools.types import ToolExecutionResult


@pytest.mark.parametrize("budget", [512, 25000])
@pytest.mark.parametrize("cursor_first", [False, True])
@pytest.mark.parametrize("cursor", ["exact-cursor", "cursor-" * 300])
def test_failed_preview游标完整或整体省略(tmp_path, budget, cursor_first, cursor):
    fields = [("next_cursor", cursor), ("content", "body" * 1000)]
    if not cursor_first:
        fields.reverse()
    original = ToolExecutionResult(False, dict(fields), error="partial operation failed")
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = render_tool_result(original, max_tokens=budget, preview_tokens=int(budget * .9), stored=stored)
    if cursor == "exact-cursor":
        assert result.output["next_cursor"] == cursor
    else:
        assert "next_cursor" not in result.output
    assert conservative_token_count(serialize_tool_result(result)) <= min(budget, 1000)
    assert "partial operation failed" in result.error


def test_failed_preview保留最终诊断退出码及metadata(tmp_path):
    diagnostic = "trace line\n" * 200 + "final diagnostic"
    original = ToolExecutionResult(False, {
        **{f"optional{i}": "z" * 300 for i in range(20)},
        "stdout": "useful stdout", "exit_code": 9, "stderr": diagnostic,
    }, error=diagnostic, metadata={"dispatched_tool": {"name": "target"}})
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = render_tool_result(original, max_tokens=25000, preview_tokens=22500, stored=stored)
    assert conservative_token_count(serialize_tool_result(result)) <= 1000
    assert "final diagnostic" in result.error
    assert result.output["exit_code"] == 9
    assert "useful stdout" in result.output["stdout"]
    assert "stderr" not in result.output
    assert result.metadata["dispatched_tool"] == {"name": "target"}


def test_failed_preview图片信封超过短目标回实际预算(tmp_path):
    from icarus.core.models.messages import ImagePart
    image = ImagePart(source="https://example.test/" + "x" * 1500, source_type="url", media_type="image/png")
    original = ToolExecutionResult(False, "x" * 3000, error="reason", images=(image,))
    stored = StoredToolResult(tmp_path / "full.txt", 10000, 10000, True)
    result = render_tool_result(original, max_tokens=5000, preview_tokens=4500, stored=stored)
    assert result.images == (image,)
    assert conservative_token_count(serialize_tool_result(result)) <= 5000
    assert result.metadata["result_file_complete"] is True


def test_preview分页恢复标量先于正文保留():
    original = ToolExecutionResult(True, {
        "content": "body" * 4000, "offset": 1, "limit": 20,
        "has_more": True, "next_offset": 21, "text_offset": 0,
        "max_chars": 2000, "truncated": True, "next_text_offset": 2000,
        "next_cursor": "exact-cursor", "page": 1, "next_page": 2,
    })
    result = render_tool_result(original, max_tokens=600, preview_tokens=540)
    for key in ("offset", "limit", "has_more", "next_offset", "text_offset",
                "max_chars", "truncated", "next_text_offset", "next_cursor", "page", "next_page"):
        assert result.output[key] == original.output[key]
    assert result.metadata["result_truncated"] is True
    assert conservative_token_count(serialize_tool_result(result)) <= 600


def test_preview超长游标不返回半截或建议重放():
    result = render_tool_result(ToolExecutionResult(True, {
        "content": "x" * 2000, "next_cursor": "cursor" * 2000,
    }), max_tokens=512, preview_tokens=460)
    assert "next_cursor" not in result.output
    assert "retry" not in serialize_tool_result(result).lower()
    assert result.metadata["result_truncated"] is True


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
    assert '"truncated": true' in encoded
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
    assert "truncated" in rendered.output
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
