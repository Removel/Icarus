import json

from apps.agent.src.agent_orchestration.tools.result_budget import (
    conservative_token_count,
    preview_json_value,
    render_tool_result,
    serialize_tool_result,
)
from apps.agent.src.agent_orchestration.tools.result_store import StoredToolResult
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult


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
