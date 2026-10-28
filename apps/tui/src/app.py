"""Application lifetime, I/O workers and serial state application."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.worker import WorkerCancelled

from apps.tui.src.configs.config import TuiConfig
from apps.tui.src.domain.chat_state import InterruptAction
from apps.tui.src.domain.commands import create_default_command_registry
from apps.tui.src.event_pipeline import create_default_projector_registry
from apps.tui.src.screens.chat.messages import (
    ChatChanged,
    DesktopCompleted,
    OperationCompleted,
    RuntimeOutputReceived,
    SubscriptionFailed,
)
from apps.tui.src.platform.opener import open_url
from apps.tui.src.screens.chat.screen import ChatScreen
from apps.tui.src.services.chat.operations import PreparedSession, execute
from apps.tui.src.services.chat.session import ChatSession
from apps.tui.src.services.desktop import DesktopService
from apps.tui.src.stores.chat import ChatStore


class IcarusTextualApp(App[int]):
    TITLE = "Icarus"
    CSS_PATH = [
        Path(__file__).parent / "styles" / name
        for name in (
            "tokens.tcss",
            "base.tcss",
            "screens/chat.tcss",
            "screens/session_picker.tcss",
            "widgets/welcome.tcss",
            "widgets/conversation.tcss",
            "widgets/input_dock.tcss",
            "widgets/command_menu.tcss",
        )
    ]

    def __init__(
        self,
        config: TuiConfig,
        *,
        client_factory,
        attachments,
        commands=None,
        projectors=None,
    ):
        super().__init__()
        self.config = config
        self.attachments = attachments
        self.desktop = DesktopService(open_url=open_url)
        self.commands = commands or create_default_command_registry()
        self.chat_store = ChatStore(
            workspace_path=config.workspace_path,
            session_id=config.session_id,
            app_version=getattr(config, "app_version", None),
        )
        self.chat_session = ChatSession(
            self.chat_store,
            client_factory,
            attachments,
            projectors or create_default_projector_registry(),
            self.commands,
            initial_session_id=config.session_id,
        )
        self.chat_screen = ChatScreen(self.chat_store, self.commands)
        self._reader = None
        self._reader_scope = None
        self._io_workers = set()
        self._cleanup_task = None
        self._paste_tokens = {}

    def get_css_variables(self):
        variables = super().get_css_variables()
        for line in (
            (Path(__file__).parent / "styles/tokens.tcss").read_text().splitlines()
        ):
            if line.startswith("$") and ":" in line:
                name, value = line.split(":", 1)
                variables[name[1:]] = value.strip().rstrip(";")
        return variables

    async def on_mount(self):
        await self.push_screen(self.chat_screen)
        self._advance(self.chat_session.start())

    def _advance(self, operations=()):
        self._io_workers = {w for w in self._io_workers if not w.is_finished}
        for operation in operations:
            worker = self.run_worker(
                self._execute(operation),
                name=f"chat-{operation.kind}",
                exit_on_error=False,
            )
            self._io_workers.add(worker)
        scope = self.chat_session.scope
        subscription = self.chat_session.subscription
        if self._reader_scope != scope:
            if self._reader:
                self._reader.cancel()
            self._reader_scope = scope
            if subscription and scope.operation_id:
                self._reader = self.run_worker(
                    self._read(subscription, scope),
                    name="chat-subscription",
                    exit_on_error=False,
                )
        self.chat_screen.post_message(ChatChanged(self.chat_store.revision))
        if self.chat_session.stopping and self._cleanup_task is None:
            self.request_shutdown(return_code=0)

    async def _execute(self, operation):
        result = await execute(operation)
        if self.chat_session.stopping:
            if (
                isinstance(result.value, PreparedSession)
                and result.value.client is not self.chat_session.client
            ):
                result.value.subscription.close()
                await result.value.client.close()
            elif operation.kind == "clipboard" and result.value and result.value.image:
                self.attachments.release((result.value.image,))
            return
        self.post_message(OperationCompleted(result))

    async def _read(self, subscription, scope):
        try:
            while True:
                update = await subscription.next_update()
                self.post_message(RuntimeOutputReceived(update, scope))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.post_message(SubscriptionFailed(scope, error))

    def on_operation_completed(self, message):
        result = message.result
        self._advance(self.chat_session.complete(result))
        if result.error is not None and result.operation.kind in {
            "clipboard",
            "cancel",
            "list",
            "prepare",
        }:
            try:
                self.notify(
                    str(result.error),
                    title="Paste failed"
                    if result.operation.kind == "clipboard"
                    else "Operation failed",
                    severity="warning",
                )
            except Exception:
                pass
        if result.operation.kind == "clipboard":
            token = self._paste_tokens.pop(result.operation.scope.operation_id, None)
            value = result.value
            if value is not None:
                if (
                    token is not None
                    and result.operation.scope.generation == self.chat_store.generation
                ):
                    self.chat_screen.deliver_clipboard(token, value)
                elif value.image:
                    self.attachments.release((value.image,))

    def on_runtime_output_received(self, message):
        self._advance(self.chat_session.receive(message.update, scope=message.scope))

    def on_subscription_failed(self, message):
        if message.scope == self.chat_session.scope and self._cleanup_task is None:
            self._advance(self.chat_session.reconnect(message.error))

    def on_copy_text_requested(self, message):
        self._desktop_worker("copy", message.text, message.generation, message.snapshot)

    def on_open_link_requested(self, message):
        self._desktop_worker("open", message.url, self.chat_store.generation)

    def _desktop_worker(self, kind, value, generation, snapshot=None):
        if self._cleanup_task is not None:
            return
        async def execute_desktop():
            error = None
            try:
                if kind == "copy":
                    await self.desktop.copy_text(value)
                else:
                    await self.desktop.open_link(value)
            except Exception as caught:
                error = caught
            self.post_message(DesktopCompleted(kind, generation, snapshot, error))
        self._io_workers.add(self.run_worker(execute_desktop(), name=f"desktop-{kind}", exit_on_error=False))

    def on_desktop_completed(self, message):
        if self._cleanup_task is None:
            self.chat_screen.deliver_desktop(message)

    def on_submit_requested(self, message):
        self._advance(self.chat_session.submit(message.submission))
        if self.chat_session.rejected:
            self.chat_screen.restore_draft(self.chat_session.rejected)

    def on_paste_requested(self, message):
        operations = self.chat_session.paste(
            request_id=message.request_id, draft_token=message.draft_token
        )
        for operation in operations:
            self._paste_tokens[operation.scope.operation_id] = message.draft_token
        self._advance(operations)

    def on_view_failed(self, message):
        self.chat_session.report_view_failure(message.error)
        self.chat_screen.post_message(ChatChanged(self.chat_store.revision))

    def on_interrupt_requested(self, message):
        action = self.chat_session.interrupt(message.has_draft)
        if action == InterruptAction.CLEAR_DRAFT:
            self.chat_screen.clear_draft()
        elif action == InterruptAction.RESTORE_PENDING:
            if self.chat_session.draft_restore:
                self.chat_screen.restore_draft(self.chat_session.draft_restore)
            self._advance()
        elif action == InterruptAction.CANCEL_ACTIVE:
            self._advance(self.chat_session.cancel_active())
        elif action == InterruptAction.EXIT:
            self.request_shutdown(return_code=0)

    def on_images_released(self, message):
        self.attachments.release(message.images)

    def resume_session(self, session_id, request_id):
        self._advance(self.chat_session.resume(session_id, request_id=request_id))

    def request_shutdown(self, *, return_code=0):
        if self._cleanup_task is not None:
            return
        self.chat_session.begin_shutdown()
        self._cleanup_task = asyncio.create_task(self._close(return_code))

    async def _close(self, return_code):
        workers = [
            w
            for w in (*self._io_workers, self._reader)
            if w is not None and not w.is_finished
        ]
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker.wait()
            except (asyncio.CancelledError, WorkerCancelled):
                pass
            except Exception:
                return_code = return_code or 1
        await self.desktop.close()
        errors = await self.chat_session.close()
        self.exit(
            result=return_code or int(bool(errors)),
            return_code=return_code or int(bool(errors)),
        )

    async def on_unmount(self):
        if self._cleanup_task is None:
            await self.desktop.close()
            await self.chat_session.close()
        elif not self._cleanup_task.done():
            await asyncio.shield(self._cleanup_task)
