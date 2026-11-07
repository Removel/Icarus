from markdown_it import MarkdownIt

from apps.tui.src.widgets.chat.citations import citation_parser


def prose(parser, text):
    return "".join(child.content for token in parser.parse(text)
                   for child in token.children or () if child.type in {"text", "code_inline"})


def test_known_sources_only_and_markdown_boundaries():
    parser = citation_parser({1, 2}, streaming=False)
    assert prose(parser, "结论[1][2]，普通[3]。") == "结论，普通[3]。"
    assert prose(parser, r"`[1]` \[1] [1](https://example.com) $x[1]$ $$y[2]$$") == (
        "[1] [1] 1 $x[1]$ $$y[2]$$"
    )
    assert parser.parse("```python\na[1]\n```")[0].content == "a[1]\n"
    assert parser.parse("    a[1]\n")[0].content == "a[1]\n"
    assert prose(parser, "$5 的价格来源[1]") == "$5 的价格来源"
    assert prose(parser, "$x[1]$ 外部引用[1]") == "$x[1]$ 外部引用"
    # Images and reference links are resolved before citation display rules.
    tokens = parser.parse("![1](https://example.com) [1][ref]\n\n[ref]: https://example.org")
    assert any(c.type == "image" for t in tokens for c in t.children or ())
    assert any(c.type == "link_open" for t in tokens for c in t.children or ())


def test_streamed_partial_reference_is_bounded_and_released_at_finish():
    parser = citation_parser({12}, streaming=True)
    assert [prose(parser, text) for text in ("结果[", "结果[1", "结果[12", "结果[12]", "结果[12]。起点[")] == [
        "结果", "结果", "结果", "结果", "结果。起点",
    ]
    assert prose(parser, "结果[12345678901234567890") == "结果[12345678901234567890"
    assert prose(citation_parser({12}, streaming=False), "起点[") == "起点["
    assert prose(citation_parser(set(), streaming=True), "普通[1") == "普通[1"


def test_parser_keeps_original_tokens_outside_citations():
    text = "**粗体** [普通链接](https://example.com)\n\n| 列 |\n| - |\n| 内容 |"
    assert citation_parser({1}, streaming=False).render(text) == MarkdownIt("gfm-like").render(text)
