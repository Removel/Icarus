"""Retained chat page: layout, focus and rendering; no network lifetime."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.geometry import Offset
from textual.screen import Screen
from textual.widgets import Button

from apps.tui.src.event_pipeline import SetRuntimeStatus, ShowNotification
from apps.tui.src.screens.chat.messages import (
    CopyTextRequested,
    OpenLinkRequested,
    ImagesReleased,
    InterruptRequested,
    PasteRequested,
    SubmitRequested,
    ViewFailed,
)
from apps.tui.src.screens.session_picker import SessionPicker
from apps.tui.src.stores.chat import ChatChange
from apps.tui.src.viewmodels.chat import build_chat_viewmodel
from apps.tui.src.widgets.chat import (
    ConversationView,
    PersistentComposer,
    TurnRail,
)
from apps.tui.src.widgets.chat.input_dock import InputDock


class ChatScreen(Screen):
    BINDINGS = [
        Binding("ctrl+c", "context_interrupt", show=False, priority=True),
        Binding("ctrl+d", "eof", show=False, priority=True),
        Binding("pageup", "page_up", show=False, priority=True),
        Binding("pagedown", "page_down", show=False, priority=True),
        Binding("ctrl+end", "latest", show=False, priority=True),
        Binding("escape", "cancel_selection", show=False, priority=True),
        Binding("ctrl+shift+s", "select_chat", show=False, priority=True),
        Binding("ctrl+shift+o", "open_links", show=False, priority=True),
    ]

    def __init__(self, store, commands):
        super().__init__()
        self.store, self.commands = store, commands
        self.rendered_revision = 0
        self.rendered_generation = store.generation
        self.activity_token = 0
        self._active = False
        self._render_lock = asyncio.Lock()
        self._clipboard = None
        self._picker_request = None
        self._render_dirty = False
        self._rendered_window_generation = 0
        self._view_failed = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="conversation-shell"):
            yield ConversationView(
                self.store.workspace_path,
                projection=self.store.conversation,
                id="conversation",
            )
            yield TurnRail(id="turn-rail")
            yield Button("↓ New messages", id="new-output")
        yield InputDock(self.commands)

    def on_mount(self):
        self._active = True
        self._responsive()
        self.query_one(PersistentComposer).focus()

    def on_resize(self):
        self._responsive()

    def _responsive(self):
        self.set_class(self.size.width <= 70, "-narrow")
        self.set_class(self.size.width <= 88, "-compact-logo")
        self.set_class(self.size.height <= 12, "-short")

    def on_screen_suspend(self):
        from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock

        for thinking in self.query(ThinkingBlock):
            thinking.set_activity(False)
        self.query_one(ConversationView).action_cancel_selection()
        self._active = False
        self.activity_token += 1
        self.query_one(ConversationView).render_valid = lambda: False

    async def on_screen_resume(self):
        self._active = True
        self.activity_token += 1
        self.query_one(ConversationView).render_valid = lambda: (
            self._active and self.app.screen is self
        )
        if self.is_mounted:
            await self.refresh_chat()
            from apps.tui.src.widgets.chat.execution_panel import ThinkingBlock

            for thinking in self.query(ThinkingBlock):
                thinking.set_activity(True)
            if self._clipboard:
                generation, token, value = self._clipboard
                self._clipboard = None
                if generation == self.store.generation:
                    self.deliver_clipboard(token, value)
                elif value.image:
                    self.app.attachments.release((value.image,))

    async def on_chat_changed(self, message):
        message.stop()
        if self._active and self.is_mounted:
            if self._view_failed:
                self.rendered_revision = self.store.revision
                return
            await self.refresh_chat()

    async def refresh_chat(self):
        try:
            await self._refresh_chat()
        except Exception as error:
            self._view_failed = True
            self.app.post_message(ViewFailed(error))
            try:
                from apps.tui.src.widgets.chat.status import TaskStatusLine

                self.query_one(TaskStatusLine).update(str(error))
            except Exception:
                pass
            self.rendered_revision = self.store.revision

    async def _refresh_chat(self):
        async with self._render_lock, self.query_one(ConversationView).render_lock:
            if self._view_failed:
                self.rendered_revision = self.store.revision
                return
            token = self.activity_token
            if not self._active or self.app.screen is not self:
                return
            view = self.query_one(ConversationView)
            if view.window_generation != self._rendered_window_generation:
                self._render_dirty = True
            revision, generation = self.store.revision, self.store.generation
            if generation != self.rendered_generation:
                view.action_cancel_selection()
                self.query_one(PersistentComposer).draft_token += 1
                if self._clipboard and self._clipboard[0] != generation:
                    old_value = self._clipboard[2]
                    self._clipboard = None
                    if old_value.image:
                        self.app.attachments.release((old_value.image,))
            view.render_valid = lambda: (
                self._active
                and self.app.screen is self
                and token == self.activity_token
                and generation == self.store.generation
            )
            changes = self.store.changes_since(self.rendered_revision)
            if self._render_dirty or (
                revision != self.rendered_revision
                and (
                    generation != self.rendered_generation
                    or self.store.conversation_revision > self.rendered_revision
                )
            ):
                if (
                    not self._render_dirty
                    and generation == self.rendered_generation
                    and self.rendered_revision > 0
                    and changes is not None
                ):
                    actions = tuple(
                        action
                        for change in changes
                        for action in change.actions
                        if not isinstance(action, (SetRuntimeStatus, ShowNotification))
                    )
                    # Leave dirty set across awaits: cancellation must not
                    # replay non-idempotent deltas against a partial update.
                    self._render_dirty = True
                    try:
                        await view.apply_change(
                            ChatChange(revision, generation, actions)
                        )
                    except Exception as error:
                        raise RuntimeError(
                            f"ConversationView action: {error}"
                        ) from error
                else:
                    start = (
                        max(0, len(self.store.conversation.turns) - 24)
                        if view.following_live_tail
                        else view.mounted_turn_range[0]
                    )
                    end = min(len(self.store.conversation.turns), start + 24)
                    snapshot = self.store.capture_window(start, end)
                    following, old_scroll = view.following_live_tail, view.scroll_y
                    self._render_dirty = True
                    try:
                        if snapshot.units:
                            await view.reconcile(snapshot)
                        else:
                            await view.reset()
                    except Exception as error:
                        raise RuntimeError(
                            f"ConversationView action: {error}"
                        ) from error
                    if (
                        token != self.activity_token
                        or not self._active
                        or self.app.screen is not self
                    ):
                        self._render_dirty = True
                        return
                    if following:
                        view.resume_follow()
                    else:
                        view._set_reading_history(True)
                        view.scroll_to(y=old_scroll, animate=False, immediate=True)
                if (
                    token != self.activity_token
                    or not self._active
                    or self.app.screen is not self
                ):
                    self._render_dirty = True
                    return
                self.rendered_revision, self.rendered_generation = revision, generation
                self._rendered_window_generation = view.window_generation
                self._render_dirty = False
            self.rendered_revision = revision
            indicator = self.query_one("#new-output", Button)
            if (
                self.store.conversation_revision
                > getattr(self, "_notified_revision", 0)
                and not view.following_live_tail
            ):
                indicator.display = True
            self._notified_revision = self.store.conversation_revision
            self._position_new_output()
            from apps.tui.src.widgets.chat.welcome import WelcomeMessage

            for hero in view.query(WelcomeMessage):
                hero.version = self.store.app_version
                hero.query_one(".welcome-version").update(
                    f"当前版本 {self.store.app_version or '未知'}   最新版本 未检查"
                )
            vm = build_chat_viewmodel(self.store)
            try:
                await self.query_one(InputDock).show_viewmodel(
                    vm, self.store.interaction.pending_items
                )
            except Exception as error:
                raise RuntimeError(f"UI rendering: {error}") from error
            self.query_one(TurnRail).set_turns(
                [t.text for t in self.store.conversation.turns],
                view.current_turn_index or 0,
            )
            ready = self.app.chat_session.sessions_ready
            if (
                ready
                and self.app.chat_session.resume_request
                and self.app.chat_session.resume_request.generation
                == self.store.generation
                and self.app.chat_session.resume_request.epoch
                == self.app.chat_session.epoch
                and ready[0] != self._picker_request
            ):
                self._picker_request = ready[0]
                self.app.push_screen(
                    SessionPicker(ready[1], current_session_id=self.store.session_id),
                    lambda selected: self.app.resume_session(selected, ready[0]),
                )
            self.call_after_refresh(view.restore_reading_offsets)
            if self.store.revision > self.rendered_revision:
                self.call_later(self.refresh_chat)

    def on_conversation_view_selection_started(self, message):
        message.stop()
        composer = self.query_one(PersistentComposer)
        composer.move_cursor(composer.cursor_location)

    def on_text_area_selection_changed(self, message):
        if isinstance(message.text_area, PersistentComposer) and message.selection.start != message.selection.end:
            self.query_one(ConversationView).action_cancel_selection()

    def on_text_area_changed(self, message):
        if isinstance(message.text_area, PersistentComposer) and message.text_area.has_focus:
            self.query_one(ConversationView).action_cancel_selection()

    def on_persistent_composer_copy_text_requested(self, message):
        message.stop()
        self.app.post_message(CopyTextRequested(message.text, message.snapshot, self.store.generation))

    def deliver_desktop(self, message):
        if message.error is not None:
            self.notify("Desktop operation failed; retry when the clipboard/browser is available.", severity="warning")
            return
        if message.kind == "open":
            return
        if message.snapshot is not None:
            current = self._active and message.generation == self.store.generation
            applied = current and self.query_one(PersistentComposer).apply_cut(message.snapshot)
            self.notify("Cut" if applied else "Copied; input changed, not cut")
        else:
            self.notify("Copied")

    def on_persistent_composer_submitted(self, message):
        message.stop()
        self.app.post_message(SubmitRequested(message.submission))

    def clear_draft(self):
        self.query_one(PersistentComposer).clear_draft()

    def restore_draft(self, submission):
        self.query_one(PersistentComposer).restore_draft(submission)

    def on_persistent_composer_image_paste_requested(self, message):
        message.stop()
        composer = self.query_one(PersistentComposer)
        self.app.post_message(
            PasteRequested(uuid4().hex, getattr(composer, "draft_token", 0))
        )

    def on_persistent_composer_images_released(self, message):
        message.stop()
        self.app.post_message(ImagesReleased(message.images))

    def deliver_clipboard(self, token, value):
        composer = self.query_one(PersistentComposer)
        if token != getattr(composer, "draft_token", 0):
            if value.image:
                self.app.attachments.release((value.image,))
            return
        if not self._active:
            self._clipboard = self.store.generation, token, value
            return
        if value.image:
            composer.attach_image(value.image.path, owned_temporary_file=True)
        elif value.text is None:
            composer.paste_text_from_clipboard()
        elif value.text:
            composer.post_message(events.Paste(value.text))

    def on_conversation_view_disclosure_requested(self, message):
        message.stop()
        self.app.chat_session.set_disclosure(
            message.task_id,
            message.kind,
            message.step,
            message.call_id,
            message.expanded,
        )

    async def on_turn_rail_turn_selected(self, message):
        message.stop()
        await self.query_one(ConversationView).jump_to_turn(message.index)

    def on_conversation_view_follow_changed(self, message):
        message.stop()
        if message.following:
            self.query_one("#new-output", Button).display = False
        self._position_new_output()

    def _position_new_output(self):
        view = self.query_one(ConversationView)
        indicator = self.query_one("#new-output", Button)
        indicator.styles.offset = Offset(
            max(0, view.region.right - 19), max(0, view.region.bottom - 2)
        )

    def on_button_pressed(self, message):
        if message.button.id == "new-output":
            self.action_latest()

    def action_context_interrupt(self):
        composer = self.query_one(PersistentComposer)
        if composer.get_selection_text() is not None:
            composer.action_copy_selection()
            return
        view = self.query_one(ConversationView)
        selected = view.get_selected_text()
        if selected is not None:
            self.app.post_message(CopyTextRequested(selected, generation=self.store.generation))
            return
        self.app.post_message(InterruptRequested(composer.has_draft))

    def on_streaming_markdown_open_link_requested(self, message):
        message.stop()
        self.app.post_message(OpenLinkRequested(message.url))

    def on_link_text_open_link_requested(self, message):
        message.stop()
        self.app.post_message(OpenLinkRequested(message.url))

    def action_cancel_selection(self):
        composer = self.query_one(PersistentComposer)
        if composer.key_handler is not None and composer.key_handler(events.Key("escape", None)):
            return
        self.query_one(ConversationView).action_cancel_selection()
        composer.move_cursor(composer.cursor_location)

    def action_select_chat(self):
        self.query_one(ConversationView).begin_keyboard_selection()

    def action_open_links(self):
        from apps.tui.src.screens.link_picker import LinkPicker

        view = self.query_one(ConversationView)
        messages = []
        from apps.tui.src.widgets.chat.markdown import StreamingMarkdown
        from apps.tui.src.widgets.chat.links import LinkText
        for widget in view.children:
            targets = []
            for text in widget.query(LinkText):
                if all(parent.display for parent in (text, *text.ancestors)):
                    targets.extend(text.link_targets())
            for markdown in widget.query(StreamingMarkdown):
                if all(parent.display for parent in (markdown, *markdown.ancestors)):
                    targets.extend(markdown.link_targets())
            if targets:
                messages.append((widget, list(dict.fromkeys(targets))))
        visible = [(widget, urls) for widget, urls in messages if widget.region.overlaps(view.region)]
        if not visible:
            self.notify("No links in the current reading area")
            return
        self.app.push_screen(LinkPicker([(f"Message {i + 1}", urls) for i, (_, urls) in enumerate(visible)]),
            lambda url: self.app.post_message(OpenLinkRequested(url)) if url else None)

    def action_eof(self):
        composer = self.query_one(PersistentComposer)
        if composer.text:
            composer.action_delete_right()
        else:
            self.app.request_shutdown(return_code=0)

    def action_page_up(self):
        self.query_one(ConversationView).page_up()

    def action_page_down(self):
        self.query_one(ConversationView).page_down()

    def action_latest(self):
        self.query_one(ConversationView).resume_follow()
        self.query_one("#new-output", Button).display = False
