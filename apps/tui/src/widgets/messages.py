"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from rich._wrap import divide_line
from rich.cells import cell_len, chop_cells
from rich.text import Text
from textual._loop import loop_last
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.content import Content, _FormattedLine
from textual.containers import Vertical
from textual.css.types import TextAlign, TextOverflow
from textual.events import Click
from textual.message import Message
from textual.selection import Selection
from textual.style import Style
from textual.widgets import Label, Markdown, Static
from textual.widgets._markdown import MarkdownParagraph


ICARUS_LOGO = """ ░▒▒░     ░▒▓▓▓▓▒░         ▒▒▒░      ░▒▒▒▒▒▒▒▒░    ░▒▒░      ░▒▒░    ░▒▓▓▓▒▒
 ▒██▒   ▒███▓▓▓▓███▒      ▓████      ▒██▓▓▓▓▓███▒  ▒██▒      ▒██▒  ▒███▓▒▓▓██▓
 ▒██▒  ▓██▓      ▓██▒    ▒██░██▓     ▒██▒     ███  ▒██▒      ▒██▒  ███     ░▓▓░
 ▒██▒ ░███              ░██▓ ░██▒    ▒██▒     ███  ▒██▒      ▒██▒  ▓██▓▒░░
 ▒██▒ ░██▓              ███   ▓██    ▒███▓▓▓████░  ▒██▒      ▒██▒   ░▒▓██████▒
 ▒██▒  ███        ░░░  ▓██████████   ▒██▓▒▒▓██▓    ▒██▒      ▒██▒         ░▓██▓
 ▒██▒  ▒██▓░     ▓██░ ░██▓░░░░░▒██▒  ▒██▒   ▒██▓   ░███░    ░███░ ░██▒     ░███
 ▒██▒   ░▓████████▓░  ███       ▓██░ ▒██▒    ▒██▓   ░▓████████▓░   ▒████▓▓███▓
  ░░       ░▒▒▒▒░     ░░░        ░░░  ░░      ░░░░     ░▒▒▒▒░        ░░▒▒▒▒░"""

_LOGO_TOP_LEFT = (184, 184, 191)
_LOGO_TOP_RIGHT = (218, 117, 129)
_LOGO_BOTTOM_LEFT = (185, 182, 189)
_LOGO_BOTTOM_RIGHT = (219, 115, 127)
_LOGO_CANVAS_WIDTH = 80
_LOGO_CANVAS_HEIGHT = 15
_LOGO_FIRST_VISIBLE_ROW = 1


def _mix_color(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    return tuple(
        round(left + (right - left) * ratio)
        for left, right in zip(start, end)
    )


def render_icarus_logo() -> Text:
    """Render the art.txt logo with its silver-to-pink true-color gradient."""

    lines = ICARUS_LOGO.splitlines()
    rendered = Text(no_wrap=True)
    for row_index, line in enumerate(lines):
        source_row = row_index + _LOGO_FIRST_VISIBLE_ROW
        vertical_ratio = source_row / (_LOGO_CANVAS_HEIGHT - 1)
        left = _mix_color(_LOGO_TOP_LEFT, _LOGO_BOTTOM_LEFT, vertical_ratio)
        right = _mix_color(_LOGO_TOP_RIGHT, _LOGO_BOTTOM_RIGHT, vertical_ratio)
        for column, character in enumerate(line):
            if character == " ":
                rendered.append(character)
                continue
            horizontal_ratio = column / (_LOGO_CANVAS_WIDTH - 1)
            red, green, blue = _mix_color(left, right, horizontal_ratio)
            rendered.append(character, style=f"rgb({red},{green},{blue})")
        if row_index < len(lines) - 1:
            rendered.append("\n")
    return rendered


_CJK_TEXT = re.compile(
    "[\u2e80-\u2fff\u3040-\u30ff\u3400-\u4dbf"
    "\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
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
            content_line = _FormattedLine(
                get_style, line, width, y=y, align=align
            )
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


class _CJKMarkdownParagraph(MarkdownParagraph):
    """Markdown paragraph that keeps CJK wrapping out of source content."""

    def set_content(self, content: Content) -> None:
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

    BLOCKS = {**Markdown.BLOCKS, "paragraph_open": _CJKMarkdownParagraph}

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


class WelcomeMessage(Vertical):
    def __init__(self, workspace_path: str | Path) -> None:
        super().__init__(classes="message welcome-message")
        self.workspace_path = Path(workspace_path).expanduser().resolve()

    def compose(self) -> ComposeResult:
        yield Static(render_icarus_logo(), classes="welcome-logo")
        yield Label("Icarus", classes="welcome-title")
        yield Static(
            f"Workspace  {self.workspace_path}",
            markup=False,
            classes="welcome-workspace",
        )
        yield Static(
            (
                "Enter submit · Shift+Enter/Ctrl+J newline · "
                "Ctrl+V image · Ctrl+C actions"
            ),
            markup=False,
            classes="welcome-help",
        )


class UserMessage(Vertical):
    def __init__(self, text: str) -> None:
        super().__init__(classes="message user-message")
        self.message_text = text

    def compose(self) -> ComposeResult:
        yield Label("You", classes="message-label")
        yield Static(self.message_text, markup=False, classes="message-content")


class _StreamingMessage(Vertical):
    def __init__(self, *, classes: str) -> None:
        super().__init__(classes=classes)
        self._markdown_parts: list[str] = []
        self._segment_finished = False
        self._markdown_stream = None

    @property
    def markdown_text(self) -> str:
        return "".join(self._markdown_parts)

    async def append_delta(self, text: str) -> None:
        if self._segment_finished:
            raise RuntimeError("Assistant message is already closed")
        if not text:
            return
        self._markdown_parts.append(text)
        if self._markdown_stream is None:
            markdown = self.query_one(StreamingMarkdown)
            self._markdown_stream = Markdown.get_stream(markdown)
        await self._markdown_stream.write(text)

    async def complete_text(self, text: str) -> None:
        """Reconcile streamed content with one complete assistant message."""

        if self._segment_finished:
            raise RuntimeError("Assistant message is already closed")
        current = self.markdown_text
        if current == text:
            return
        if text.startswith(current):
            await self.append_delta(text[len(current) :])
            return
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()
        self._markdown_parts = [text]
        await self.query_one(StreamingMarkdown).update(text)

    async def finish(self) -> None:
        if self._segment_finished:
            return
        self._segment_finished = True
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()

    async def on_unmount(self) -> None:
        await self.finish()


class AssistantMessage(_StreamingMessage):
    def __init__(self) -> None:
        super().__init__(classes="message assistant-message")

    def compose(self) -> ComposeResult:
        yield Label("Icarus", classes="message-label")
        yield StreamingMarkdown("", classes="assistant-markdown")


class AssistantProgressBlock(_StreamingMessage):
    def __init__(self, step: int) -> None:
        super().__init__(classes="assistant-progress")
        self.step = step

    def compose(self) -> ComposeResult:
        yield Label(
            f"Progress · step {self.step}", classes="message-label"
        )
        yield StreamingMarkdown("", classes="assistant-markdown")


class DisclosureSummary(Static):
    """Keyboard and pointer accessible disclosure summary."""

    can_focus = True
    BINDINGS = [
        Binding("enter", "toggle", show=False),
        Binding("space", "toggle", show=False),
    ]

    class Toggled(Message):
        pass

    def action_toggle(self) -> None:
        self.post_message(self.Toggled())

    def on_click(self, event: Click) -> None:
        event.stop()
        self.focus()
        self.action_toggle()


class ThinkingBlock(Vertical):
    def __init__(
        self, *, step: int, expanded: bool = True, historical: bool = False
    ) -> None:
        super().__init__(classes="thinking-block")
        self.step = step
        self.expanded = expanded
        self.historical = historical
        self.partial = False
        self.completed = False
        self._markdown_parts: list[str] = []
        self._markdown_stream = None

    @property
    def markdown_text(self) -> str:
        return "".join(self._markdown_parts)

    def compose(self) -> ComposeResult:
        yield DisclosureSummary(
            self._summary_text(),
            markup=False,
            classes="disclosure-summary thinking-summary",
        )
        body = StreamingMarkdown("", classes="thinking-content")
        body.display = self.expanded
        yield body

    async def append_delta(self, text: str) -> None:
        if self.completed or not text:
            return
        self._markdown_parts.append(text)
        if self._markdown_stream is None:
            self._markdown_stream = Markdown.get_stream(
                self.query_one(StreamingMarkdown)
            )
        await self._markdown_stream.write(text)

    async def complete_text(self, text: str, *, partial: bool) -> None:
        if self.completed:
            return
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()
        self._markdown_parts = [text]
        self.partial = partial
        self.completed = True
        await self.query_one(StreamingMarkdown).update(text)
        self._refresh_summary()

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        if self.is_mounted:
            self.query_one(StreamingMarkdown).display = expanded
            self._refresh_summary()

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def _summary_text(self) -> str:
        marker = "▾" if self.expanded else "▸"
        status = " · partial" if self.partial else ""
        return f"{marker} Thinking · step {self.step}{status}"

    def _refresh_summary(self) -> None:
        if self.is_mounted:
            self.query_one(DisclosureSummary).update(self._summary_text())

    def on_disclosure_summary_toggled(
        self, event: DisclosureSummary.Toggled
    ) -> None:
        event.stop()
        self.toggle()

    async def finish(self) -> None:
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()

    async def on_unmount(self) -> None:
        await self.finish()


class ToolBlock(Vertical):
    def __init__(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments_json: str = "{}",
    ) -> None:
        super().__init__(classes="tool-block is-running")
        self.call_id = call_id
        self.tool_name = tool_name
        self.arguments_json = arguments_json
        self.success: bool | None = None
        self.error: str | None = None
        self.output_preview: object = None
        self.preview_truncated = False
        self.full_result_available = False
        self.preview_error: str | None = None
        self.expanded = False

    def compose(self) -> ComposeResult:
        yield DisclosureSummary(
            self._summary_text(),
            markup=False,
            classes="disclosure-summary tool-summary",
        )
        details = Static(
            self._details_text(), markup=False, classes="tool-details"
        )
        details.display = self.expanded
        yield details

    def complete(
        self,
        *,
        success: bool,
        error: str | None = None,
        output_preview: object = None,
        preview_truncated: bool = False,
        full_result_available: bool = False,
        preview_error: str | None = None,
    ) -> None:
        self.success = success
        self.error = error if not success else None
        self.output_preview = output_preview
        self.preview_truncated = preview_truncated
        self.full_result_available = full_result_available
        self.preview_error = preview_error
        self.remove_class("is-running")
        self.set_class(success, "is-success")
        self.set_class(not success, "is-failed")
        self._refresh_content()

    def interrupt(self) -> None:
        if self.success is not None:
            return
        self.remove_class("is-running")
        self.add_class("is-interrupted")
        self._refresh_content()

    @property
    def state_text(self) -> str:
        if self.success is True:
            return "completed"
        if self.success is False:
            return "failed"
        if self.has_class("is-interrupted"):
            return "interrupted"
        return "running"

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        if self.is_mounted:
            self.query_one(".tool-details", Static).display = expanded
            self.query_one(DisclosureSummary).update(self._summary_text())

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def on_disclosure_summary_toggled(
        self, event: DisclosureSummary.Toggled
    ) -> None:
        event.stop()
        self.toggle()

    def _summary_text(self) -> str:
        marker = "▾" if self.expanded else "▸"
        hint = _argument_hint(self.arguments_json)
        detail = f" · {hint}" if hint else ""
        return f"{marker} {self.tool_name}{detail} · {self.state_text}"

    def _details_text(self) -> str:
        sections = [
            "Arguments\n" + _format_json_text(self.arguments_json)
        ]
        if self.output_preview is not None:
            sections.append("Output\n" + _format_preview(self.output_preview))
        if self.error:
            sections.append("Error\n" + self.error)
        if self.preview_error:
            sections.append("Preview unavailable")
        if self.preview_truncated and self.full_result_available:
            sections.append(
                "Output preview truncated. Full result remains on the Agent side."
            )
        elif self.preview_truncated:
            sections.append(
                "Output preview truncated. Full result is unavailable."
            )
        elif self.full_result_available:
            sections.append("Full result remains on the Agent side.")
        return "\n\n".join(sections)

    def _refresh_content(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(DisclosureSummary).update(self._summary_text())
        self.query_one(".tool-details", Static).update(self._details_text())


class UserCorrectionBlock(Vertical):
    def __init__(self, text: str, applied_before_step: int) -> None:
        super().__init__(classes="user-correction-block")
        self.correction_text = text
        self.applied_before_step = applied_before_step

    def compose(self) -> ComposeResult:
        yield Static(
            f"Added by you before step {self.applied_before_step}",
            markup=False,
            classes="correction-label",
        )
        yield Static(
            self.correction_text,
            markup=False,
            classes="correction-content",
        )


class RunCard(Vertical):
    def __init__(self, task_id: str) -> None:
        super().__init__(classes="message run-card")
        self.task_id = task_id

    def compose(self) -> ComposeResult:
        yield Static("Run", markup=False, classes="run-card-label")


def _format_json_text(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2)


def _argument_hint(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    for key in ("path", "file", "file_path", "command", "query"):
        item = parsed.get(key)
        if isinstance(item, (str, int, float, bool)):
            text = str(item).replace("\n", " ")
            return text if len(text) <= 72 else text[:69] + "..."
    return ""


def _format_preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float, list, dict)):
        try:
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, indent=2
            )
        except (TypeError, ValueError):
            return "[Preview unavailable]"
    return "[Preview unavailable]"


class ErrorMessage(Vertical):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(classes="message error-message")
        self.error_type = error_type
        self.error_message = message

    def compose(self) -> ComposeResult:
        yield Label("Error", classes="message-label")
        yield Static(
            f"{self.error_type}: {self.error_message}",
            markup=False,
            classes="message-content",
        )


class TurnStatusMessage(Static):
    def __init__(self, status: str) -> None:
        super().__init__(
            f"Task {status}",
            markup=False,
            classes=f"message turn-status turn-{status}",
        )
