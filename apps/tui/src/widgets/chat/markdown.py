"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

import re

from rich._wrap import divide_line
from rich.cells import cell_len, chop_cells
from textual._loop import loop_last
from textual.await_complete import AwaitComplete
from textual.content import Content, _FormattedLine
from textual.css.types import TextAlign, TextOverflow
from textual.message import Message
from textual.selection import Selection
from textual.style import Style
from textual.widgets import Markdown
from textual.widgets._markdown import MarkdownParagraph
from apps.tui.src.widgets.chat.links import DesktopLinkClick, url_ranges

_CJK_TEXT = re.compile(
    "[\u2e80-\u2fff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def _cjk_wrap_units(text: str) -> list[tuple[int, str]]:
    """Split prose into English words and individually breakable CJK chars."""

    units: list[tuple[int, str]] = []
    unit_start = 0
    unit: list[str] = []

    def flush() -> None:
        nonlocal unit_start
        if unit:
            units.append((unit_start, "".join(unit)))
            unit.clear()

    for index, character in enumerate(text):
        if character.isspace():
            if not unit:
                unit_start = index
            unit.append(character)
            flush()
        elif _CJK_TEXT.fullmatch(character):
            flush()
            units.append((index, character))
            unit_start = index + 1
        else:
            if not unit:
                unit_start = index
            unit.append(character)
    flush()

    grouped_units: list[tuple[int, str]] = []
    index = 0
    while index < len(units):
        start, unit_text = units[index]
        if (
            index + 1 < len(units)
            and re.fullmatch(r"\d+(?:[.,]\d+)?\s*", unit_text)
            and _CJK_TEXT.fullmatch(units[index + 1][1])
        ):
            unit_text += units[index + 1][1]
            index += 1
        grouped_units.append((start, unit_text))
        index += 1
    return grouped_units


def _divide_cjk_line(text: str, width: int) -> list[int]:
    """Return character offsets which wrap mixed prose by terminal cells."""

    if width <= 0:
        return []

    offsets: list[int] = []
    cell_offset = 0
    for start, unit in _cjk_wrap_units(text):
        unit_width = cell_len(unit.rstrip())
        if unit_width <= width - cell_offset:
            cell_offset += cell_len(unit)
            continue

        if unit_width > width:
            if cell_offset and start:
                offsets.append(start)
            chunks = [chunk for chunk in chop_cells(unit, width) if chunk]
            chunk_start = start
            for last, chunk in loop_last(chunks):
                if not last:
                    chunk_start += len(chunk)
                    if chunk_start:
                        offsets.append(chunk_start)
                else:
                    cell_offset = cell_len(chunk)
        elif cell_offset and start:
            offsets.append(start)
            cell_offset = cell_len(unit)
        else:
            cell_offset = cell_len(unit)

    return list(dict.fromkeys(offset for offset in offsets if offset < len(text)))


class _CJKWrappingContent(Content):
    """Textual Content with cell-aware break opportunities for CJK prose."""

    @property
    def without_spans(self) -> Content:
        if self.spans:
            return _CJKWrappingContent(
                self.plain,
                [],
                self.cell_length,
                strip_control_codes=False,
            )
        return self

    def _wrap_and_format(
        self,
        width: int,
        align: TextAlign = "left",
        overflow: TextOverflow = "fold",
        no_wrap: bool = False,
        line_pad: int = 0,
        tab_size: int = 8,
        selection: Selection | None = None,
        selection_style: Style | None = None,
        post_style: Style | None = None,
        get_style=Style.parse,
    ) -> list[_FormattedLine]:
        if no_wrap or not _CJK_TEXT.search(self.plain):
            return super()._wrap_and_format(
                width,
                align=align,
                overflow=overflow,
                no_wrap=no_wrap,
                line_pad=line_pad,
                tab_size=tab_size,
                selection=selection,
                selection_style=selection_style,
                post_style=post_style,
                get_style=get_style,
            )

        output_lines: list[_FormattedLine] = []
        get_span = selection.get_span if selection is not None else lambda _y: None

        for y, line in enumerate(self.split(allow_blank=True)):
            if post_style is not None:
                line = line.stylize(post_style)
            if selection_style is not None and (span := get_span(y)) is not None:
                start, end = span
                line = line.stylize(
                    selection_style,
                    start,
                    len(line.plain) if end == -1 else end,
                )

            line = line.expand_tabs(tab_size)
            content_line = _FormattedLine(get_style, line, width, y=y, align=align)
            available_width = max(1, width - line_pad * 2)
            offsets = (
                _divide_cjk_line(line.plain, available_width)
                if overflow == "fold"
                else divide_line(line.plain, available_width, fold=False)
            )
            divided_lines = content_line.content.divide(offsets)
            ellipsis = overflow == "ellipsis"
            divided_lines = [
                (
                    part.truncate(width, ellipsis=ellipsis)
                    if last
                    else part.rstrip().truncate(width, ellipsis=ellipsis)
                )
                for last, part in loop_last(divided_lines)
            ]
            formatted_lines = [
                _FormattedLine(
                    get_style,
                    part.rstrip_end(width).pad(line_pad, line_pad),
                    width,
                    offset,
                    y,
                    align=align,
                )
                for part, offset in zip(divided_lines, [0, *offsets])
            ]
            formatted_lines[-1].line_end = True
            output_lines.extend(formatted_lines)

        return output_lines


class _CJKMarkdownParagraph(DesktopLinkClick, MarkdownParagraph):
    """Markdown paragraph that keeps CJK wrapping out of source content."""

    def set_content(self, content: Content) -> None:
        from rich.style import Style as RichStyle

        # Inline code and explicit links already carry spans. Linkify only prose
        # outside those spans, preserving declared Markdown link destinations.
        for start, end, url in url_ranges(content.plain):
            if not any(span.start < end and span.end > start for span in content.spans):
                content = content.stylize(Style.from_rich_style(RichStyle(
                    underline=True, meta={"@click": f"link({url!r})"})), start, end)
        super().set_content(
            _CJKWrappingContent(
                content.plain,
                list(content.spans),
                content.cell_length,
                strip_control_codes=False,
            )
        )


class StreamingMarkdown(Markdown):
    """Keep stale blocks out of Textual's mouse-selection path."""

    # Every prose block can contain a Markdown link (headings/table cells too).
    # The shared click arbiter prevents its first click opening before a second
    # click establishes text selection. Fences remain plain selectable code.
    BLOCKS = {
        name: type(f"Desktop{block.__name__}", (DesktopLinkClick, block), {})
        for name, block in Markdown.BLOCKS.items()
        if name not in {"fence", "code_block", "paragraph_open"}
    }
    BLOCKS.update({"fence": Markdown.BLOCKS["fence"], "code_block": Markdown.BLOCKS["code_block"], "paragraph_open": _CJKMarkdownParagraph})

    def __init__(self, *args, **kwargs):
        kwargs["open_links"] = False
        super().__init__(*args, **kwargs)

    class OpenLinkRequested(Message):
        def __init__(self, url):
            super().__init__()
            self.url = url

    def on_markdown_link_clicked(self, event):
        event.stop()
        if not self.screen.get_selected_text():
            self.post_message(self.OpenLinkRequested(event.href))

    def link_targets(self):
        # Parse tokens to avoid fenced/inline code; never expose source offsets
        # as rendered selection offsets. This list is keyboard navigation only.
        from markdown_it import MarkdownIt

        urls = []
        for token in MarkdownIt().parse(self._markdown):
            if token.type != "inline":
                continue
            depth = 0
            for child in token.children or ():
                if child.type == "link_open":
                    depth += 1
                    href = child.attrGet("href") or ""
                    if href.lower().startswith(("http://", "https://")):
                        urls.append(href)
                elif child.type == "link_close":
                    depth -= 1
                elif child.type == "text" and not depth:
                    urls.extend(url for _, _, url in url_ranges(child.content))
        return list(dict.fromkeys(urls))

    class ContentAppended(Message):
        """The streamed fragment has been rendered into Markdown blocks."""

    def update(self, markdown: str) -> AwaitComplete:
        for child in self.walk_children():
            child.ALLOW_SELECT = False
        return super().update(markdown)

    def append(self, markdown: str) -> AwaitComplete:
        previous_tail = self.children[-1] if self.children else None
        if previous_tail is not None:
            previous_tail.ALLOW_SELECT = False
        append = super().append(markdown)

        async def await_append() -> None:
            try:
                await append
            finally:
                if previous_tail is not None and previous_tail.parent is not None:
                    previous_tail.ALLOW_SELECT = True
            self.post_message(self.ContentAppended())

        return AwaitComplete(await_append())
