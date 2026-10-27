"""Textual application shell for the Icarus terminal client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App
from textual.css.query import NoMatches

from apps.tui.src.platform.clipboard import read_clipboard_image, read_clipboard_text
from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.gateway_client.ports import ClientFactory
from apps.tui.src.messages import (
    DispatchFinished,
    RuntimeOutputReceived,
    RuntimeReconnected,
    RuntimeStarted,
    RuntimeStartFailed,
    RuntimeSubscriptionFailed,
    SessionActivationRequested,
    TaskStatusReceived,
)
from apps.tui.src.screens.chat import ChatScreen
from apps.tui.src.services.session_service import SessionService
from apps.tui.src.services.types import PreparedSession

__all__ = [
    "IcarusTextualApp",
    "DispatchFinished",
    "RuntimeOutputReceived",
    "RuntimeReconnected",
    "RuntimeStarted",
    "RuntimeStartFailed",
    "RuntimeSubscriptionFailed",
    "SessionActivationRequested",
    "TaskStatusReceived",
    "PreparedSession",
]


_FORWARDED_MESSAGES = (
    DispatchFinished,
    RuntimeOutputReceived,
    RuntimeReconnected,
    RuntimeStarted,
    RuntimeStartFailed,
    RuntimeSubscriptionFailed,
    SessionActivationRequested,
    TaskStatusReceived,
)

_SCREEN_DELEGATED_ATTRS = {
    "chat_state",
    "_last_sequence",
    "_connection_epoch",
    "_session_has_user_input",
    "_fatal_failure",
    "_fatal_message",
}


class IcarusTextualApp(App[int]):
    """Thin application shell that owns the process lifecycle."""

    CSS_PATH = "styles.tcss"
    TITLE = "Icarus"

    def __init__(
        self,
        config: TuiConfig | None = None,
        *,
        runtime_factory: ClientFactory | None = None,
        initial_session_id: str | None = None,
        workspace_path: str | Path | None = None,
        resource_root: str | Path | None = None,
        projector_registry=None,
        command_registry=None,
    ) -> None:
        super().__init__()
        if config is None:
            if workspace_path is None:
                raise TypeError("config or workspace_path is required")
            config = TuiConfig(
                workspace_path=Path(workspace_path).expanduser().resolve(),
                session_id=initial_session_id,
                resource_root=(
                    Path(resource_root).expanduser().resolve()
                    if resource_root is not None
                    else None
                ),
            )
        self.config = config
        self.runtime_factory = runtime_factory
        self.chat_screen: ChatScreen | None = None
        self._projector_registry = projector_registry
        self._command_registry = command_registry

    def build_screen(self) -> ChatScreen:
        return ChatScreen(
            self.config,
            runtime_factory=self.runtime_factory,
            clipboard_reader=read_clipboard_image,
            clipboard_text_reader=read_clipboard_text,
            projector_registry=self._projector_registry,
            command_registry=self._command_registry,
        )

    def on_mount(self) -> None:
        screen = self.build_screen()
        self.chat_screen = screen
        self.push_screen(screen)

    def post_message(self, message):
        screen = self.__dict__.get("chat_screen")
        if (
            screen is not None
            and isinstance(message, _FORWARDED_MESSAGES)
            and not getattr(message, "_tui_from_screen", False)
        ):
            return screen.post_message(message)
        return super().post_message(message)

    def query_one(self, selector, expect_type=None):
        screen = self.__dict__.get("chat_screen")
        if screen is not None:
            try:
                return screen.query_one(selector, expect_type)
            except NoMatches:
                pass
        return super().query_one(selector, expect_type)

    def query(self, selector):
        screen = self.__dict__.get("chat_screen")
        if screen is not None:
            return screen.query(selector)
        return super().query(selector)

    @staticmethod
    def _status_is_idle(status) -> bool:
        return SessionService._status_is_idle(status)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(
            ("on_", "_on_", "watch_", "action_", "compute_")
        ):
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}"
            )
        screen = self.__dict__.get("chat_screen")
        if screen is not None:
            return getattr(screen, name)
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _SCREEN_DELEGATED_ATTRS:
            screen = self.__dict__.get("chat_screen")
            if screen is not None:
                setattr(screen, name, value)
                return
        super().__setattr__(name, value)
