"""Single source of chat facts; no terminal objects or network operations."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass

from apps.tui.src.domain.chat_state import ChatState
from apps.tui.src.event_pipeline import UiAction
from apps.tui.src.stores.conversation import ConversationProjection, DisplayUnit


@dataclass(frozen=True)
class ChatChange:
    revision: int
    generation: int
    actions: tuple[UiAction, ...] = ()
    history_reset: bool = False


@dataclass(frozen=True)
class ConversationWindowSnapshot:
    generation: int
    revision: int
    start: int
    end: int
    units: tuple[DisplayUnit, ...]


class ChatStore:
    def __init__(self, *, workspace_path="", session_id=None, app_version=None):
        self.interaction = ChatState()
        self.conversation = ConversationProjection()
        self.generation = 0
        self.revision = 0
        self.last_change: ChatChange | None = None
        self._changes: deque[ChatChange] = deque(maxlen=512)
        self.conversation_revision = 0
        self.workspace_path = str(workspace_path)
        self.session_id = session_id
        self.session_title = "新对话"
        self.app_version = app_version
        self.status_message = ""
        self.fatal_message = ""
        self.has_user_input = False
        self.model_label = None
        self.reasoning_label = None
        self.context_label = None

    def changed(self, actions=(), *, history_reset=False):
        self.revision += 1
        self.last_change = ChatChange(
            self.revision, self.generation, tuple(actions), history_reset
        )
        if history_reset:
            self._changes.clear()
        self._changes.append(self.last_change)
        return self.last_change

    def changes_since(self, revision):
        """Return ordered catch-up changes, or require a window snapshot."""
        if revision == self.revision:
            return ()
        if not self._changes or revision < self._changes[0].revision - 1:
            return None
        changes = tuple(change for change in self._changes if change.revision > revision)
        if not changes or any(change.history_reset for change in changes):
            return None
        return changes

    def apply_actions(self, actions: tuple[UiAction, ...], *, historical=False):
        from apps.tui.src.event_pipeline import (
            AppendUserCorrection,
            AppendUserMessage,
            SetRuntimeStatus,
            ShowNotification,
        )

        for action in actions:
            if isinstance(action, (AppendUserMessage, AppendUserCorrection)):
                self.has_user_input = True
            if isinstance(action, AppendUserMessage) and self.session_title == "新对话":
                self.session_title = " ".join(action.text.split())[:40] or "新对话"
            if isinstance(action, (SetRuntimeStatus, ShowNotification)):
                continue
            if not self.conversation.apply(action):
                raise TypeError(
                    f"Unhandled presentation action: {type(action).__name__}"
                )
        change = self.changed(actions)
        if any(
            not isinstance(action, (SetRuntimeStatus, ShowNotification))
            for action in actions
        ):
            self.conversation_revision = change.revision
        return change

    def reset_session(self, *, generation):
        self.generation = generation
        self.interaction = ChatState()
        self.conversation.reset()
        self.has_user_input = False
        self.session_title = "新对话"
        self.status_message = self.fatal_message = ""
        change = self.changed(history_reset=True)
        self.conversation_revision = change.revision
        return change

    def capture_window(self, start, end):
        units = [u for turn in self.conversation.turns[start:end] for u in turn.units]
        if not self.conversation.turns:
            units = self.conversation.units
        return ConversationWindowSnapshot(
            self.generation, self.revision, start, end, tuple(deepcopy(units))
        )
