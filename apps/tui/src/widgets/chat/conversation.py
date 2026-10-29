"""Application-internal scrolling conversation projection."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import events
from textual.containers import VerticalScroll
from textual.message import Message

from apps.tui.src.event_pipeline import (
    AppendAssistantDelta,
    AppendError,
    AppendThinkingDelta,
    AppendToolStarted,
    AppendUserCorrection,
    AppendUserMessage,
    CompleteAssistantMessage,
    CompleteThinking,
    FinishTurn,
    UiAction,
    UpdateToolCompleted,
)
from apps.tui.src.stores.conversation import (
    DisplayUnit,
)
from apps.tui.src.widgets.chat.execution_panel import RunCard, ThinkingBlock, ToolBlock
from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
from apps.tui.src.widgets.chat.message_cards import (
    AssistantMessage,
    AssistantProgressBlock,
    ErrorMessage,
    TurnStatusMessage,
    UserCorrectionBlock,
    UserMessage,
)
from textual.selection import Selection
from textual.geometry import Offset
from apps.tui.src.widgets.chat.selection import ConversationSelection
from apps.tui.src.widgets.chat.welcome import WelcomeMessage


class ConversationView(VerticalScroll):
    """Render only conversation-target UiActions."""

    class FollowChanged(Message):
        """The view started or stopped following the live tail."""

        def __init__(self, following: bool) -> None:
            self.following = following
            super().__init__()

    def __init__(
        self, workspace_path: str | Path, *, projection, id: str | None = None
    ) -> None:
        super().__init__(id=id)
        self.workspace_path = Path(workspace_path).expanduser().resolve()
        self.projection = projection
        self._history_worker = None
        self.render_valid = lambda: True
        self.render_lock = asyncio.Lock()
        self._window_start = 0
        self._window_end = 0
        self._window_widgets: list[tuple[int, object]] = []
        self._rendered_turn: int | None = None
        self._history_deferred = False
        self._detached_window = False
        self._window_loading = False
        self.window_generation = 0
        self._reading_history = False
        self._live_tail_task: str | None = None
        self._live_tail_widgets: list[object] = []
        self._run_cards: dict[str, RunCard] = {}
        self._assistant_candidates: dict[
            tuple[str, int], AssistantMessage | AssistantProgressBlock
        ] = {}
        self._candidate_completed: set[tuple[str, int]] = set()
        self._completed_thinking: set[tuple[str, int]] = set()
        self._thinking: dict[tuple[str, int], ThinkingBlock] = {}
        self._tools: dict[tuple[str, str], ToolBlock] = {}
        self._visible_turns: list[tuple[int, list[object]]] = []
        self._anchor_pending = True
        self._restoring_history = False
        self._following = True
        self._reading_offsets = {}
        self._selection = ConversationSelection()


    def _selection_bodies(self):
        from textual.widgets import Static
        from textual.widgets._markdown import MarkdownBlock

        # MarkdownBlock._content is the rendered Content. Public Markdown source
        # cannot represent emphasis/code/table display offsets; keep this adapter
        # local to the pinned Textual 8.x renderer rather than slicing source.
        groups = self._visible_turns or [(0, list(self.children))]
        for turn, widgets in groups:
            unit_counts = {}
            for root in widgets:
                if isinstance(root, WelcomeMessage):
                    continue
                unit_kind = type(root).__name__
                ordinal = unit_counts.get(unit_kind, 0)
                unit_counts[unit_kind] = ordinal + 1
                unit_index = (unit_kind, ordinal)
                body_ordinals = {}
                for widget in root.walk_children():
                    if not widget.display or not widget.visible or any(not parent.display for parent in widget.ancestors):
                        continue
                    text = None
                    if isinstance(widget, MarkdownBlock):
                        content = getattr(widget, "_content", None)
                        if content is not None and content.plain:
                            text = content.plain
                    elif isinstance(widget, Static) and any(widget.has_class(name) for name in ("message-content", "correction-content", "tool-details")):
                        value = widget.get_selection(Selection(None, None))
                        text = value[0] if value else None
                    if text is None:
                        continue
                    identity = ("body", 0, "")
                    for parent in (widget, *widget.ancestors):
                        if isinstance(parent, ThinkingBlock):
                            identity = ("thinking", parent.step, "")
                            break
                        if isinstance(parent, ToolBlock):
                            identity = ("tool", 0, parent.call_id)
                            break
                    ordinal = body_ordinals.get(identity, 0)
                    body_ordinals[identity] = ordinal + 1
                    yield (turn, unit_index, identity, ordinal), widget, text

    def _capture_selection(self):
        if not self.is_mounted:
            return
        bodies = list(self._selection_bodies())
        mounted_turns = {turn for turn, _ in self._visible_turns}
        visible_keys = {key for key, _, _ in bodies}
        hidden = {key for key in self._selection.fragments if key[0] in mounted_turns and key not in visible_keys}
        if hidden:
            for key in hidden:
                self._selection.fragments.pop(key, None)
            if self._selection.anchor and self._selection.anchor[0] in hidden:
                self._selection.anchor = None
                self._selection.focus = None
                self._selection.keyboard = False
            self._selection._native_signature = tuple((id(widget), value) for widget, value in self.screen.selections.items())
        if not self._selection.keyboard and not self._selection.dragging:
            self._selection.capture(bodies, self.screen.selections)
        if self._selection.active:
            self._set_reading_history(True)

    def _body_at_pointer(self, event):
        widget, offset = self.screen.get_widget_and_offset_at(event.screen_x, event.screen_y)
        bodies = list(self._selection_bodies())
        for key, body, text in bodies:
            if widget is body or (widget is not None and body in widget.ancestors):
                from apps.tui.src.widgets.chat.selection import offset_index
                return bodies, key, offset_index(text, offset)
        return None

    def on_mouse_down(self, event):
        if event.button != 1:
            return
        # Screen has already created its native pointer anchor. Clear only the
        # semantic state here; clearing Screen would disable edge auto-scroll.
        self._selection.clear()
        found = self._body_at_pointer(event)
        if found:
            bodies, key, index = found
            self._selection.anchor = (key, index)
            self._selection.focus = (key, index)
            self._selection.dragging = True
            self._set_reading_history(True)
            self.post_message(self.SelectionStarted())

    def on_mouse_move(self, event):
        if not self._selection.dragging:
            return
        found = self._body_at_pointer(event)
        if found:
            bodies, key, index = found
            self._selection._remember_order(bodies)
            anchor = self._selection.anchor
            if anchor is not None and (self._selection.order[key], index) >= (self._selection.order[anchor[0]], anchor[1]):
                index += 1
            self._selection.extend(bodies, key, index)
        if self._window_start > 0 and event.screen_y <= self.region.y + 1 and not self._window_loading:
            self.run_worker(self._page_window(-8), group="selection-page", exclusive=True)
        elif self._window_end < self.turn_count and event.screen_y >= self.region.bottom - 2 and not self._window_loading:
            self.run_worker(self._page_window(8), group="selection-page", exclusive=True)

    def on_mouse_up(self, event):
        if self._selection.dragging:
            self._selection.dragging = False
            self.call_after_refresh(self._restore_selection_highlights)

    class SelectionStarted(Message):
        pass

    def begin_keyboard_selection(self):
        bodies = list(self._selection_bodies())
        visible = [entry for entry in bodies if entry[1].region.overlaps(self.region)]
        if not visible:
            return
        self.action_cancel_selection()
        key, widget, text = visible[0]
        self._selection.keyboard = True
        self._selection.anchor = (key, 0)
        self._selection.extend(bodies, key, min(1, len(text)))
        self._set_reading_history(True)
        self.focus()
        self.post_message(self.SelectionStarted())
        self._restore_selection_highlights()

    async def on_key(self, event):
        if not self._selection.keyboard or event.key not in {
            "left", "right", "up", "down", "shift+left", "shift+right", "shift+up", "shift+down", "pageup", "pagedown", "shift+pageup", "shift+pagedown"}:
            return
        event.stop()
        event.prevent_default()
        backwards = event.key.endswith(("left", "up", "pageup"))
        bodies = list(self._selection_bodies())
        keys = [key for key, _, _ in bodies]
        focus_key, index = self._selection.focus
        if focus_key not in keys:
            return
        position = keys.index(focus_key)
        text = bodies[position][2]
        step = 1 if event.key.endswith(("left", "right")) else max(1, min(len(text), self.size.width))
        index += -step if backwards else step
        if index < 0 and position == 0 and self._window_start:
            await self._page_window(-8)
            bodies = list(self._selection_bodies())
            keys = [key for key, _, _ in bodies]
            position = keys.index(focus_key)
        elif index > len(text) and position == len(bodies) - 1 and self._window_end < self.turn_count:
            await self._page_window(8)
            bodies = list(self._selection_bodies())
            keys = [key for key, _, _ in bodies]
            position = keys.index(focus_key)
        if index < 0 and position:
            position -= 1
            focus_key, widget, text = bodies[position]
            index = len(text)
        elif index > len(text) and position + 1 < len(bodies):
            position += 1
            focus_key, widget, text = bodies[position]
            index = 0
        index = max(0, min(len(text), index))
        if not event.key.startswith("shift+"):
            self._selection.anchor = (focus_key, index)
        self._selection.extend(bodies, focus_key, index)
        self.scroll_to_widget(bodies[position][1], animate=False, immediate=True)
        self._restore_selection_highlights()

    def has_selection(self) -> bool:
        self._capture_selection()
        return self._selection.active

    def get_selected_text(self) -> str | None:
        self._capture_selection()
        return self._selection.text()

    def action_cancel_selection(self):
        self._selection.clear()
        if self.is_mounted:
            self.screen.clear_selection()

    def _restore_selection_highlights(self):
        if not self._selection.active:
            return
        selections = {}
        for key, widget, text in self._selection_bodies():
            body = self._selection.fragments.get(key)
            if body is not None and text.startswith(body.text[:body.end]):
                def location(index):
                    prefix = text[:index]
                    return Offset(len(prefix.rsplit("\n", 1)[-1]), prefix.count("\n"))
                selections[widget] = Selection(location(body.start), location(body.end))
        self.screen.selections = selections
        self._selection._native_signature = tuple((id(widget), value) for widget, value in selections.items())
        if self._selection.dragging:
            self._remap_native_drag_anchor()

    def _remap_native_drag_anchor(self):
        from textual.selection import SelectStart, SelectState

        if self._selection.anchor is None:
            return
        key, index = self._selection.anchor
        for body_key, widget, text in self._selection_bodies():
            if body_key != key:
                continue
            prefix = text[:index]
            offset = Offset(len(prefix.rsplit("\n", 1)[-1]), prefix.count("\n"))
            pointer = widget.content_region.offset
            start = SelectStart(widget.parent, pointer - widget.parent.region.offset,
                widget.parent.region.offset, widget.parent.scroll_offset,
                content_widget=widget, content_offset=offset)
            self.screen._select_state = SelectState(pointer, start)
            break


    async def apply_change(self, change):
        for action in change.actions:
            if not self.render_valid():
                return
            await self.apply_action(action)

    @property
    def following_live_tail(self) -> bool:
        """Whether new output keeps the newest content in view on its own."""

        return not self._detached_window and not self._reading_history

    def _set_reading_history(self, reading: bool) -> None:
        if reading:
            self._anchor_pending = False
            self.anchor(False)
        if self._reading_history != reading:
            self._reading_history = reading
            self._announce_follow_state()

    def _set_detached_window(self, detached: bool) -> None:
        if self._detached_window != detached:
            self._detached_window = detached
            self._announce_follow_state()

    def _announce_follow_state(self) -> None:
        following = self.following_live_tail
        if following != self._following:
            self._following = following
            self.post_message(self.FollowChanged(following))

    def on_disclosure_changed(self, message):
        message.stop()
        self._report_disclosure(message.block, message.expanded)

    def _report_disclosure(self, block, expanded):
        for unit in self.projection.units:
            if unit.kind != "run_card":
                continue
            for child in unit.children:
                if (
                    child.kind == "thinking"
                    and self._thinking.get((unit.task_id, child.step)) is block
                ) or (
                    child.kind == "tool"
                    and self._tools.get((unit.task_id, child.call_id)) is block
                ):
                    self.post_message(
                        self.DisclosureRequested(
                            unit.task_id,
                            child.kind,
                            child.step,
                            child.call_id,
                            expanded,
                        )
                    )

    class DisclosureRequested(Message):
        def __init__(self, task_id, kind, step, call_id, expanded):
            super().__init__()
            self.task_id, self.kind, self.step, self.call_id, self.expanded = (
                task_id,
                kind,
                step,
                call_id,
                expanded,
            )

    async def on_mount(self) -> None:
        await self.mount(WelcomeMessage(self.workspace_path))
        self.watch(self.screen, "selections", self._capture_selection, init=False)

    async def reset(self) -> None:
        """Replace the current Session projection with an empty one."""

        self.action_cancel_selection()
        await self._finish_all_streams()
        self._window_start = self._window_end = 0
        self._window_widgets.clear()
        self._rendered_turn = None
        self._history_deferred = False
        self._set_detached_window(False)
        self._window_loading = False
        self._set_reading_history(False)
        self._live_tail_task = None
        self._live_tail_widgets.clear()
        self._run_cards.clear()
        self._assistant_candidates.clear()
        self._candidate_completed.clear()
        self._completed_thinking.clear()
        self._thinking.clear()
        self._tools.clear()
        self._visible_turns.clear()
        self._restoring_history = False
        self.display = True
        self.anchor(False)
        self._anchor_pending = True
        await self.remove_children()
        await self.mount(WelcomeMessage(self.workspace_path))
        self.scroll_home(animate=False, immediate=True)

    async def append_user_message(self, text: str, *, task_id: str = "") -> None:
        if self._detached_window:
            if self._live_tail_task and self._live_tail_task != task_id:
                await self._clear_live_tail()
            self._live_tail_task = task_id
            widget = UserMessage(text)
            await self.mount(widget)
            self._live_tail_widgets.append(widget)
            return
        index = self.projection.turn_index(task_id)
        if index is None:
            index = self.turn_count - 1
        if len(self._visible_turns) >= 24:
            if self._reading_history or self._selection.active or self._selection.dragging:
                self._set_detached_window(True)
                return
            old_index, old_widgets = self._visible_turns.pop(0)
            detached = not self.is_vertical_scroll_end
            old_scroll = self.scroll_y
            self._save_disclosures(old_index, old_widgets)
            self._forget_widgets(old_widgets)
            for widget in old_widgets:
                if widget.is_mounted:
                    await widget.remove()
            self._window_start = old_index + 1
            if detached:
                self.anchor(False)
                self._set_detached_window(True)
                self.scroll_to(y=old_scroll, animate=False, immediate=True)
        widget = UserMessage(text)
        await self.mount(widget)
        self._visible_turns.append((index, [widget]))
        self._window_end = index + 1
        self._rendered_turn = index
        if not self._restoring_history:
            self._activate_anchor_after_layout()

    async def _clear_live_tail(self) -> None:
        widgets = self._live_tail_widgets
        self._live_tail_widgets = []
        self._live_tail_task = None
        self._forget_widgets(widgets)
        for widget in widgets:
            if widget.is_mounted:
                await widget.remove()

    def _save_disclosures(self, index: int, widgets: list[object]) -> None:
        # The projection's owner handles disclosure intents. Do not mutate Store
        # models from window eviction; this only records what the widgets report.
        for widget in widgets:
            if isinstance(widget, RunCard):
                for block in widget.walk_children():
                    if isinstance(block, (ThinkingBlock, ToolBlock)):
                        self._report_disclosure(block, block.expanded)

    def _forget_widgets(self, widgets: list[object]) -> None:
        """Remove references to evicted Textual subtrees; retain projection state."""
        top = set(widgets)
        self._window_widgets = [
            entry for entry in self._window_widgets if entry[1] not in top
        ]
        self._run_cards = {
            key: card
            for key, card in self._run_cards.items()
            if not any(
                card is widget or card in widget.walk_children() for widget in top
            )
        }
        self._assistant_candidates = {
            key: candidate
            for key, candidate in self._assistant_candidates.items()
            if not any(
                candidate is widget or candidate in widget.walk_children()
                for widget in top
            )
        }
        self._thinking = {
            key: thinking
            for key, thinking in self._thinking.items()
            if not any(
                thinking is widget or thinking in widget.walk_children()
                for widget in top
            )
        }
        self._tools = {
            key: tool
            for key, tool in self._tools.items()
            if not any(
                tool is widget or tool in widget.walk_children() for widget in top
            )
        }

    def begin_history_restore(self) -> None:
        self._restoring_history = True
        self._history_deferred = True
        self._window_start = 0
        self._window_end = 0
        self._rendered_turn = None
        self.display = False

    def finish_history_restore(self) -> None:
        self._restoring_history = False
        self.display = True
        self._history_worker = self.run_worker(
            self._show_latest_window(), exclusive=True, group="conversation-window"
        )

    async def finish_history_restore_async(self) -> None:
        self._restoring_history = False
        await self._show_latest_window()
        self.display = True

    async def _show_latest_window(self) -> None:
        count = self.turn_count
        if self._history_deferred:
            if count:
                await self._render_window(max(0, count - 24), count)
                self._rendered_turn = count - 1
            else:
                await self._render_untagged_units()
        self._history_deferred = False
        self.resume_follow()

    async def _render_untagged_units(self) -> None:
        for unit in self.projection.units:
            await self._mount_unit(unit)

    @property
    def turn_count(self) -> int:
        return len(self.projection.turns)

    @property
    def current_turn_index(self) -> int | None:
        return self._rendered_turn

    @property
    def mounted_turn_range(self) -> tuple[int, int]:
        return self._window_start, self._window_end

    async def jump_to_turn(self, index: int) -> None:
        self.action_cancel_selection()
        if self._history_worker is not None and not self._history_worker.is_finished:
            await self._history_worker.wait()
        if not 0 <= index < self.turn_count:
            raise IndexError(index)
        if self._window_start <= index < self._window_end:
            self._rendered_turn = index
            self.anchor(False)
            user = next(
                (
                    widget
                    for turn, group in self._visible_turns
                    if turn == index
                    for widget in group
                    if isinstance(widget, UserMessage)
                ),
                None,
            )
            if user is not None:
                self.scroll_to_widget(user, animate=False, immediate=True)
            return
        start = min(max(0, index - 12), max(0, self.turn_count - 24))
        end = min(self.turn_count, start + 24)
        await self._render_window(start, end)
        self._rendered_turn = index
        self.anchor(False)
        user = next(
            (
                widget
                for turn, widget in self._window_widgets
                if turn == index and isinstance(widget, UserMessage)
            ),
            None,
        )
        if user is not None:
            self.scroll_to_widget(user, animate=False, immediate=True)

    async def _render_window(self, start: int, end: int) -> None:
        self._capture_selection()
        async with self.render_lock:
            self.window_generation += 1
            with self.app.batch_update():
                await self._replace_window(start, end)
        self.call_after_refresh(self._restore_selection_highlights)

    async def _replace_window(self, start: int, end: int) -> None:
        from copy import deepcopy

        window_units = [
            (index, deepcopy(self.projection.snapshot_for_turn(index)))
            for index in range(start, end)
        ]
        for index, widgets in self._visible_turns:
            self._save_disclosures(index, widgets)
        await self._finish_all_streams()
        self._run_cards.clear()
        self._assistant_candidates.clear()
        self._candidate_completed.clear()
        self._completed_thinking.clear()
        self._thinking.clear()
        self._tools.clear()
        await self.remove_children()
        self._live_tail_widgets.clear()
        self._live_tail_task = None
        self._window_widgets.clear()
        self._visible_turns.clear()
        if start == 0:
            await self.mount(WelcomeMessage(self.workspace_path))
        for index, units in window_units:
            if not self.render_valid():
                return
            turn_widgets = []
            for unit in units:
                widget = await self._mount_unit(unit)
                self._window_widgets.append((index, widget))
                turn_widgets.append(widget)
            self._visible_turns.append((index, turn_widgets))
        self._window_start, self._window_end = start, end
        self._set_detached_window(end < self.turn_count)

    def _unit_key(self, unit, ordinal):
        return (unit.task_id, unit.kind, unit.step, unit.call_id, unit.segment, ordinal)

    def _capture_local_offsets(self):
        start, end = self.mounted_turn_range
        units = [
            unit for turn in self.projection.turns[start:end] for unit in turn.units
        ]
        widgets = [
            widget for widget in self.children if not isinstance(widget, WelcomeMessage)
        ]
        for ordinal, (widget, unit) in enumerate(zip(widgets, units)):
            if not hasattr(widget, "_unit_key"):
                widget._unit_key = self._unit_key(unit, ordinal)
        for widget in self.children:
            key = getattr(widget, "_unit_key", None)
            if key is not None:
                self._reading_offsets[key] = [
                    (node.scroll_x, node.scroll_y)
                    for node in widget.walk_children()
                    if isinstance(node, VerticalScroll)
                ]

    def restore_reading_offsets(self):
        if not self.render_valid():
            return
        for widget in self.children:
            offsets = self._reading_offsets.get(getattr(widget, "_unit_key", None), ())
            nodes = [
                node
                for node in widget.walk_children()
                if isinstance(node, VerticalScroll)
            ]
            for node, (x, y) in zip(nodes, offsets):
                if hasattr(node, "restore_offset"):
                    node.restore_offset(x, y)
                else:
                    node.scroll_to(x=x, y=y, animate=False, immediate=True)

    async def reconcile(self, snapshot):
        self._capture_local_offsets()
        # Keep the previous frame painted until replacement is ready. Hiding the
        # view across awaits exposes a blank frame and can outlive cancellation.
        with self.app.batch_update():
            await self._reconcile_window(snapshot)

    async def _reconcile_window(self, snapshot):
        from copy import deepcopy
        saved_selection = deepcopy(self._selection)
        await self.reset()
        self._selection = saved_selection
        if self._closing or not self.is_attached:
            return
        await self.remove_children()
        if snapshot.start == 0:
            await self.mount(WelcomeMessage(self.workspace_path))
        self._visible_turns.clear()
        for ordinal, unit in enumerate(snapshot.units):
            if self._closing or not self.is_attached or not self.render_valid():
                return
            widget = await self._mount_unit(unit)
            if widget is None or not self.render_valid():
                return
            widget._unit_key = self._unit_key(unit, ordinal)
            index = self.projection.turn_index(unit.task_id)
            if index is not None:
                group = next((g for i, g in self._visible_turns if i == index), None)
                if group is None:
                    group = []
                    self._visible_turns.append((index, group))
                group.append(widget)
        if not self.render_valid():
            return
        self._window_start, self._window_end = snapshot.start, snapshot.end
        self._set_detached_window(snapshot.end < self.turn_count)
        self._rendered_turn = snapshot.end - 1 if snapshot.end else None
        self.call_after_refresh(self.restore_reading_offsets)
        self.call_after_refresh(self._restore_selection_highlights)

    async def _mount_unit(self, unit: DisplayUnit):
        if not self.render_valid():
            return None
        if unit.kind == "user":
            widget = UserMessage(unit.text)
            await self.mount(widget)
        elif unit.kind in {"assistant", "progress"}:
            widget = (
                AssistantMessage()
                if unit.kind == "assistant"
                else AssistantProgressBlock(unit.step)
            )
            await self.mount(widget)
            await widget.complete_text(unit.text)
            if unit.completed or unit.kind == "progress":
                await widget.finish()
            self._assistant_candidates[(unit.task_id, unit.step)] = widget
            if unit.completed:
                self._candidate_completed.add((unit.task_id, unit.step))
        elif unit.kind == "run_card":
            widget = RunCard(unit.task_id)
            await self.mount(widget)
            if not unit.completed:
                self._run_cards[unit.task_id] = widget
            for child in unit.children:
                if not self.render_valid():
                    return widget
                if child.kind == "thinking":
                    thinking = ThinkingBlock(
                        step=child.step, expanded=child.expanded, historical=True
                    )
                    await widget.mount_record(thinking)
                    self._thinking.update(
                        {
                            (unit.task_id, step): block
                            for step, block in widget.thinking.items()
                        }
                    )
                    self._thinking[(unit.task_id, child.step)] = thinking
                    if child.completed:
                        await thinking.complete_text(child.text, partial=child.partial)
                    else:
                        await thinking.append_delta(child.text)
                elif child.kind == "tool":
                    tool = ToolBlock(
                        call_id=child.call_id,
                        tool_name=child.tool_name,
                        arguments_json=child.arguments_json,
                    )
                    await widget.mount_record(tool)
                    self._thinking.update(
                        {(unit.task_id, step): block for step, block in widget.thinking.items()}
                    )
                    self._tools[(unit.task_id, child.call_id)] = tool
                    if child.status in {"completed", "failed"}:
                        tool.complete(
                            success=child.status == "completed",
                            error=child.error,
                            output_preview=child.output_preview,
                            preview_truncated=child.preview_truncated,
                            full_result_available=child.full_result_available,
                            preview_error=child.preview_error,
                        )
                    elif child.status == "interrupted":
                        tool.interrupt()
                    tool.set_expanded(child.expanded)
                elif child.kind == "correction":
                    await widget.mount(
                        UserCorrectionBlock(child.text, child.applied_before_step or 0)
                    )
                elif child.kind == "progress":
                    progress = AssistantProgressBlock(child.step)
                    await widget.mount(progress)
                    await progress.complete_text(child.text)
                    await progress.finish()
        elif unit.kind == "correction":
            widget = UserCorrectionBlock(unit.text, unit.applied_before_step or 0)
            await self.mount(widget)
        elif unit.kind == "error":
            widget = ErrorMessage(unit.status, unit.text)
            await self.mount(widget)
        elif unit.kind == "turn_status":
            widget = TurnStatusMessage(unit.status)
            await self.mount(widget)
        else:
            raise ValueError(f"Unknown display unit: {unit.kind}")
        return widget

    async def apply_action(self, action: UiAction) -> bool:
        self._capture_selection()
        if self._restoring_history:
            if isinstance(action, AppendUserMessage) and self.turn_count >= 23:
                self._history_deferred = True
            if self._history_deferred:
                return True
        if (
            self._detached_window
            and (self._live_tail_task != action.task_id or self._live_tail_task is None)
            and not isinstance(action, AppendUserMessage)
        ):
            return True
        if isinstance(action, AppendUserMessage):
            await self.append_user_message(action.text, task_id=action.task_id)
        elif isinstance(action, AppendAssistantDelta):
            if (action.task_id, action.step) in self._candidate_completed:
                return True

            assistant = await self._ensure_assistant_candidate(
                action.task_id, action.step
            )
            await assistant.append_delta(action.text)
        elif isinstance(action, CompleteAssistantMessage):
            key = (action.task_id, action.step)
            if key in self._candidate_completed:
                return True

            assistant = await self._ensure_assistant_candidate(
                action.task_id, action.step
            )
            await assistant.complete_text(action.text)
            await assistant.finish()
            self._candidate_completed.add(key)
            self._run_cards.pop(action.task_id, None)
        elif isinstance(action, AppendThinkingDelta):
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                thinking = await self._ensure_thinking(
                    action.task_id,
                    action.step,
                    historical=action.historical,
                )
                await thinking.append_delta(action.text)
        elif isinstance(action, CompleteThinking):
            key = (action.task_id, action.step)
            if key not in self._completed_thinking:
                thinking = await self._ensure_thinking(
                    action.task_id,
                    action.step,
                    historical=action.historical,
                )
                await thinking.complete_text(action.text, partial=action.partial)
                self._completed_thinking.add(key)
        elif isinstance(action, AppendUserCorrection):
            await self.mount(
                UserCorrectionBlock(action.text, action.applied_before_step)
            )
        elif isinstance(action, AppendToolStarted):
            card = await self._ensure_run_card(action.task_id)
            tool = ToolBlock(
                call_id=action.call_id,
                tool_name=action.tool_name,
                arguments_json=action.arguments_json,
            )
            self._tools[(action.task_id, action.call_id)] = tool
            await card.mount_record(tool)
            self._thinking.update(
                {(action.task_id, step): block for step, block in card.thinking.items()}
            )
        elif isinstance(action, UpdateToolCompleted):
            key = (action.task_id, action.call_id)
            tool = self._tools.get(key)
            if tool is None:
                card = await self._ensure_run_card(action.task_id)
                tool = ToolBlock(
                    call_id=action.call_id,
                    tool_name=action.tool_name,
                )
                self._tools[key] = tool
                await card.mount_record(tool)
                self._thinking.update(
                    {(action.task_id, step): block for step, block in card.thinking.items()}
                )
            tool.complete(
                success=action.success,
                error=action.error,
                output_preview=action.output_preview,
                preview_truncated=action.preview_truncated,
                full_result_available=action.full_result_available,
                preview_error=action.preview_error,
            )
        elif isinstance(action, AppendError):
            await self.mount(ErrorMessage(action.error_type, action.message))
        elif isinstance(action, FinishTurn):
            for (task_id, _), thinking in self._thinking.items():
                if task_id == action.task_id:
                    thinking.set_running(False)
            if action.status == "interrupted":
                for (task_id, _), tool in self._tools.items():
                    if task_id == action.task_id:
                        tool.interrupt()
            await self._finish_task_candidates(action.task_id)
            if action.status in {"failed", "cancelled", "interrupted"}:
                await self.mount(TurnStatusMessage(action.status))
            await self._remove_empty_run_card(action.task_id)
        else:
            return False

        if self._detached_window and self._live_tail_task == action.task_id:
            recorded = set(self._live_tail_widgets)
            mounted = {
                existing for _, group in self._visible_turns for existing in group
            }
            for widget in self.children:
                if widget not in recorded and widget not in mounted:
                    self._live_tail_widgets.append(widget)
        if (
            self._visible_turns
            and not isinstance(action, AppendUserMessage)
            and not self._detached_window
        ):
            current_index, widgets = self._visible_turns[-1]
            if self.projection.turn_index(action.task_id) == current_index:
                for widget in self.children:
                    if widget not in widgets and widget not in (
                        w for _, group in self._visible_turns[:-1] for w in group
                    ):
                        widgets.append(widget)
        if not self._restoring_history:
            self._activate_anchor_after_layout()
        if self._selection.active:
            self.call_after_refresh(self._restore_selection_highlights)
        return True

    async def _ensure_run_card(self, task_id: str) -> RunCard:
        card = self._run_cards.get(task_id)
        if card is None:
            card = RunCard(task_id)
            self._run_cards[task_id] = card
            live_candidate = next(
                (
                    candidate
                    for (candidate_task, candidate_step), candidate in (
                        self._assistant_candidates.items()
                    )
                    if candidate_task == task_id
                    and isinstance(candidate, AssistantMessage)
                    and (candidate_task, candidate_step)
                    not in self._candidate_completed
                    and candidate.parent is self
                ),
                None,
            )
            if live_candidate is None:
                await self.mount(card)
            else:
                await self.mount(card, before=live_candidate)
        return card

    async def _ensure_assistant_candidate(
        self, task_id: str, step: int
    ) -> AssistantMessage | AssistantProgressBlock:
        key = (task_id, step)
        assistant = self._assistant_candidates.get(key)
        if assistant is None:
            assistant = AssistantMessage()
            self._assistant_candidates[key] = assistant
            await self.mount(assistant)
        return assistant

    async def _ensure_thinking(
        self, task_id: str, step: int, *, historical: bool
    ) -> ThinkingBlock:
        key = (task_id, step)
        thinking = self._thinking.get(key)
        if thinking is None:
            card = await self._ensure_run_card(task_id)
            thinking = ThinkingBlock(
                step=step,
                expanded=False,
                historical=historical,
            )
            self._thinking[key] = thinking
            await card.mount_record(thinking)
            self._thinking.update(
                {(task_id, number): block for number, block in card.thinking.items()}
            )
        return self._thinking[key]

    async def _finish_task_candidates(self, task_id: str) -> None:
        keys = [key for key in self._assistant_candidates if key[0] == task_id]
        for key in sorted(keys):
            if key in self._candidate_completed:
                candidate = self._assistant_candidates.get(key)
                if candidate is not None:
                    await candidate.finish()
            else:
                candidate = self._assistant_candidates.get(key)
                if candidate is not None:
                    await candidate.finish()
        for key in keys:
            self._assistant_candidates.pop(key, None)
            self._candidate_completed.discard(key)

    async def _remove_empty_run_card(self, task_id: str) -> None:
        card = self._run_cards.get(task_id)
        if card is None:
            return
        if not self._run_card_is_meaningful(card):
            self._run_cards.pop(task_id, None)
            await card.remove()

    @staticmethod
    def _run_card_is_meaningful(card: RunCard) -> bool:
        return any(
            isinstance(
                child,
                (
                    ThinkingBlock,
                    ToolBlock,
                    UserCorrectionBlock,
                    AssistantProgressBlock,
                ),
            )
            for child in card.walk_children()
        )

    async def _finish_all_streams(self) -> None:
        for assistant in self._assistant_candidates.values():
            await assistant.finish()
        for thinking in self._thinking.values():
            await thinking.finish()

    async def _resume_latest_window(self) -> None:
        start = max(0, self.turn_count - 24)
        await self._render_window(start, self.turn_count)
        self._rendered_turn = self.turn_count - 1
        self.resume_follow()

    def page_up(self) -> None:
        self._set_reading_history(True)
        if self._window_start > 0 and self.scroll_y <= self.size.height:
            self.run_worker(
                self._page_window(-8), exclusive=True, group="conversation-window"
            )
            return
        self.scroll_page_up(animate=False)

    def page_down(self) -> None:
        if self._window_end < self.turn_count and (
            self.max_scroll_y - self.scroll_y <= self.size.height
        ):
            self.run_worker(
                self._page_window(8), exclusive=True, group="conversation-window"
            )
            return
        self.scroll_page_down(
            animate=False,
            on_complete=self._restore_follow_if_at_end,
        )

    async def _page_window(self, delta: int) -> None:
        if self._window_loading:
            return
        self._window_loading = True
        try:
            start = max(0, min(self.turn_count - 24, self._window_start + delta))
            if start == self._window_start:
                return
            top = self._top_visible_turn()
            await self._render_window(start, min(self.turn_count, start + 24))
            self.anchor(False)
            if top is not None:
                index, offset = top
                user = next(
                    (
                        widget
                        for n, widget in self._window_widgets
                        if n == index and isinstance(widget, UserMessage)
                    ),
                    None,
                )
                if user is not None:
                    self.scroll_to(
                        y=max(
                            0, user.region.y - self.region.y - offset + self.scroll_y
                        ),
                        animate=False,
                        immediate=True,
                    )
            self._rendered_turn = (
                self._top_visible_turn()[0] if self._top_visible_turn() else start
            )
        finally:
            self._window_loading = False

    def _top_visible_turn(self) -> tuple[int, int] | None:
        for index, widget in self._window_widgets:
            if isinstance(widget, UserMessage) and widget.region.bottom > self.region.y:
                return index, widget.region.y - self.region.y
        return None

    def resume_follow(self) -> None:
        self._set_reading_history(False)
        if self._detached_window and self.turn_count:
            self.run_worker(
                self._resume_latest_window(),
                exclusive=True,
                group="conversation-window",
            )
            return
        if self.max_scroll_y <= 0:
            self.anchor(False)
            self._anchor_pending = True
            self.scroll_home(animate=False, immediate=True)
            return
        self._anchor_pending = False
        self.anchor()
        self.scroll_end(
            animate=False,
            immediate=True,
            x_axis=False,
        )

    def action_page_up(self) -> None:
        self.page_up()

    def action_page_down(self) -> None:
        self.page_down()

    def action_scroll_down(self) -> None:
        self.scroll_down(
            animate=False,
            on_complete=self._restore_follow_if_at_end,
        )

    def action_scroll_end(self) -> None:
        self.resume_follow()

    def _restore_follow_if_at_end(self) -> None:
        if self.max_scroll_y <= 0:
            self._anchor_pending = True
        elif self.is_vertical_scroll_end:
            self._anchor_pending = False
            # Scrolling back to the tail is the reader returning to the newest
            # output, so the history-reading state ends with it.
            self._set_reading_history(False)
            self.anchor()

    def _activate_anchor_after_layout(self) -> None:
        if self._anchor_pending:
            self.call_after_refresh(self._activate_anchor_if_scrollable)

    def _activate_anchor_if_scrollable(self) -> None:
        if self._anchor_pending and self.max_scroll_y > 0 and self.render_valid():
            self._anchor_pending = False
            self.anchor()

    def on_streaming_markdown_content_appended(
        self, event: StreamingMarkdown.ContentAppended
    ) -> None:
        event.stop()
        if not self._restoring_history:
            self._activate_anchor_after_layout()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._window_start > 0 and self.scroll_y <= self.size.height:
            self._set_reading_history(True)
            self.run_worker(
                self._page_window(-8), exclusive=True, group="conversation-window"
            )
            event.stop()
            return
        self._set_reading_history(True)
        super()._on_mouse_scroll_up(event)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._window_end < self.turn_count and (
            self.max_scroll_y - self.scroll_y <= self.size.height
        ):
            self.run_worker(
                self._page_window(8), exclusive=True, group="conversation-window"
            )
            event.stop()
            return
        super()._on_mouse_scroll_down(event)
        self.call_after_refresh(self._restore_follow_if_at_end)
