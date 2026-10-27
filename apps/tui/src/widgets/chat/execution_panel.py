"""Focused message widgets rendered inside the conversation view."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widgets import Markdown, Static

from apps.tui.src.widgets.chat.markdown import StreamingMarkdown


class DisclosureChanged(Message):
    def __init__(self, block, expanded):
        super().__init__()
        self.block = block
        self.expanded = expanded


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
        self, *, step: int, expanded: bool = False, historical: bool = False
    ) -> None:
        super().__init__(classes="thinking-block")
        self.step = step
        self.expanded = expanded
        self.historical = historical
        self.archived = False
        self.partial = False
        self.completed = False
        self._spin_frame = 0
        self._spin_timer = None
        self._markdown_parts: list[str] = []
        self._markdown_stream = None
        self._render_generation = 0
        self._rendered_text = ""
        self._render_lock = asyncio.Lock()

    def on_mount(self):
        self._spin_timer = self.set_interval(0.18, self._spin)
        self.set_activity(not self.historical or not self.completed)

    def set_activity(self, active):
        if self._spin_timer is not None:
            if (
                active
                and not self.completed
                and not self.archived
                and not getattr(
                    getattr(self.app, "config", None), "reduced_motion", False
                )
            ):
                self._spin_timer.resume()
            else:
                self._spin_timer.pause()

    def _spin(self):
        if not self.completed and not self.archived and self.is_on_screen:
            self._spin_frame = (self._spin_frame + 1) % 4
            self._refresh_summary()

    def set_running(self, running):
        self.completed = not running
        self.set_activity(running)
        self._refresh_summary()

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
        if not self.expanded:
            return
        if self._markdown_stream is None:
            if self._rendered_text != self.markdown_text[: -len(text)]:
                await self.query_one(StreamingMarkdown).update(self.markdown_text)
                self._rendered_text = self.markdown_text
                return
            self._markdown_stream = Markdown.get_stream(
                self.query_one(StreamingMarkdown)
            )
        await self._markdown_stream.write(text)
        self._rendered_text = self.markdown_text

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
        self.set_activity(False)
        async with self._render_lock:
            if self.expanded and self._rendered_text != text:
                await self.query_one(StreamingMarkdown).update(text)
                self._rendered_text = text
        self._refresh_summary()

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded == expanded:
            return
        self.expanded = expanded
        self.post_message(DisclosureChanged(self, expanded).set_sender(self))
        self._render_generation += 1
        if self.is_mounted:
            self.query_one(StreamingMarkdown).display = expanded
            if expanded and self._rendered_text != self.markdown_text:
                self.run_worker(self._render_body(self._render_generation))
            self._refresh_summary()

    async def _render_body(self, generation: int) -> None:
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()
        async with self._render_lock:
            if (
                generation != self._render_generation
                or not self.is_mounted
                or not self.expanded
            ):
                return
            text = self.markdown_text
            if text != self._rendered_text:
                await self.query_one(StreamingMarkdown).update(text)
                self._rendered_text = text

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def _summary_text(self) -> str:
        marker = "▾" if self.expanded else "▸"
        status = " · partial" if self.partial else ""
        spinner = "✓" if self.completed else ("◐", "◓", "◑", "◒")[self._spin_frame]
        return f"{marker} {spinner} Thinking · step {self.step}{status}"

    def _refresh_summary(self) -> None:
        if self.is_mounted:
            self.query_one(DisclosureSummary).update(self._summary_text())

    def on_disclosure_summary_toggled(self, event: DisclosureSummary.Toggled) -> None:
        event.stop()
        self.toggle()

    async def finish(self) -> None:
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            await stream.stop()

    async def on_unmount(self) -> None:
        if self._spin_timer:
            self._spin_timer.stop()
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
        details = Static(self._details_text(), markup=False, classes="tool-details")
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
            self.post_message(DisclosureChanged(self, expanded).set_sender(self))
            self.query_one(".tool-details", Static).display = expanded
            self.query_one(DisclosureSummary).update(self._summary_text())

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def on_disclosure_summary_toggled(self, event: DisclosureSummary.Toggled) -> None:
        event.stop()
        self.toggle()

    def _summary_text(self) -> str:
        marker = "▾" if self.expanded else "▸"
        hint = _argument_hint(self.arguments_json)
        detail = f" · {hint}" if hint else ""
        return f"{marker} {self.tool_name}{detail} · {self.state_text}"

    def _details_text(self) -> str:
        sections = ["Arguments\n" + _format_json_text(self.arguments_json)]
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
            sections.append("Output preview truncated. Full result is unavailable.")
        elif self.full_result_available:
            sections.append("Full result remains on the Agent side.")
        return "\n\n".join(sections)

    def _refresh_content(self) -> None:
        if not self.is_mounted:
            return
        self.query_one(DisclosureSummary).update(self._summary_text())
        self.query_one(".tool-details", Static).update(self._details_text())


class ToolRecords(VerticalScroll):
    def __init__(self):
        super().__init__(classes="tool-records")
        self.following = True

    def _size_updated(self, size, virtual_size, container_size, layout=True):
        changed = super()._size_updated(size, virtual_size, container_size, layout)
        # New disclosure/tool layout can grow after mount callbacks have run.
        # Follow the final measured extent, not an earlier scroll maximum.
        if self.following:
            self.scroll_to(y=self.max_scroll_y, animate=False, immediate=True)
        return changed

    def _on_mouse_scroll_up(self, event):
        if self.scroll_y > 0:
            self.following = False
            super()._on_mouse_scroll_up(event)
            event.stop()

    def _on_mouse_scroll_down(self, event):
        if self.scroll_y < self.max_scroll_y:
            super()._on_mouse_scroll_down(event)
            event.stop()
            self.following = self.is_vertical_scroll_end


class RunCard(Vertical):
    """One run: fixed-height archived execution log, then latest Thinking."""

    def __init__(self, task_id: str) -> None:
        super().__init__(classes="message run-card")
        self.task_id = task_id
        self.thinking: dict[int, ThinkingBlock] = {}
        self.latest_thinking: ThinkingBlock | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="execution-records-frame"):
            yield Static("执行记录", markup=False, classes="run-card-label")
            yield ToolRecords()
        yield Vertical(classes="current-thinking")

    async def _copy_thinking(self, source, parent):
        # Textual remove() disposes the stream/timers. Reconstruct the moved
        # disclosure from its full text rather than reusing an unmounted node.
        copy = ThinkingBlock(
            step=source.step, expanded=source.expanded, historical=source.historical
        )
        await parent.mount(copy)
        if source.completed:
            await copy.complete_text(source.markdown_text, partial=source.partial)
        else:
            await copy.append_delta(source.markdown_text)
        await source.remove()
        self.thinking[copy.step] = copy
        return copy

    async def mount_record(self, widget):
        records = self.query_one(ToolRecords)
        if isinstance(widget, ThinkingBlock):
            latest = self.latest_thinking
            if latest is not None and widget.step > latest.step:
                archived = await self._copy_thinking(latest, records)
                archived.archived = True
                archived.set_activity(False)
                self.latest_thinking = None
            if latest is None or widget.step > latest.step:
                await self.query_one(".current-thinking", Vertical).mount(widget)
                self.latest_thinking = widget
            else:
                widget.archived = True
                await records.mount(widget)
                widget.set_activity(False)
            self.thinking[widget.step] = widget
        else:
            await records.mount(widget)
        if records.following:
            records.call_after_refresh(
                lambda: records.scroll_end(animate=False, immediate=True)
            )


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
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        except (TypeError, ValueError):
            return "[Preview unavailable]"
    return "[Preview unavailable]"
