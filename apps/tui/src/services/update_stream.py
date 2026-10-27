"""Framework-free consumption and projection of public RuntimeUpdate values."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import logging
from typing import Any

from packages.gateway_protocol import RuntimeUpdateModel
from apps.tui.src.domain.chat_state import ChatState
from apps.tui.src.event_pipeline import SetRuntimeStatus, UiAction
from apps.tui.src.event_pipeline.dispatcher import ProjectorRegistry
from apps.tui.src.gateway_client.ports import RuntimeClient


logger = logging.getLogger("icarus.tui.update_stream")


class UpdateFatal(RuntimeError):
    """A RuntimeUpdate cannot be projected without violating stream order."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__(f"{stage}: {cause}")
        self.stage = stage
        self.cause = cause


class UpdateStream:
    """Own sequence gates, early buffering and projection state."""

    def __init__(
        self,
        projectors: ProjectorRegistry,
        chat_state: ChatState,
        *,
        session_scope: Callable[[], tuple[str | None, str | None]],
        on_actions: Callable[[tuple[UiAction, ...]], Awaitable[None]],
        on_fatal: Callable[[str, BaseException], None],
        on_user_input: Callable[[], None],
    ) -> None:
        self._projectors = projectors
        self._chat_state = chat_state
        self._session_scope = session_scope
        self._on_actions = on_actions
        self._on_fatal = on_fatal
        self._on_user_input = on_user_input
        self.last_sequence = 0
        self._completed_assistant_steps: set[tuple[str | None, Any]] = set()
        self._early_updates: dict[str, list[RuntimeUpdateModel]] = {}

    def reset(self, last_sequence: int = 0) -> None:
        self.last_sequence = last_sequence
        self._completed_assistant_steps.clear()
        self._early_updates.clear()

    def bind_chat_state(self, chat_state: ChatState) -> None:
        """Use the current queue state after a Session switch replaces it."""

        self._chat_state = chat_state

    def buffer(self, update: RuntimeUpdateModel) -> None:
        if update.task_id is not None:
            self._early_updates.setdefault(update.task_id, []).append(update)

    def take_early(self) -> tuple[RuntimeUpdateModel, ...]:
        updates = [
            update
            for buffered in self._early_updates.values()
            for update in buffered
        ]
        self._early_updates.clear()
        updates.sort(
            key=lambda update: (
                update.sequence is None,
                update.sequence or 0,
                update.occurred_at,
            )
        )
        return tuple(updates)

    async def project(
        self,
        update: RuntimeUpdateModel,
        *,
        historical: bool = False,
    ) -> tuple[UiAction, ...]:
        if (
            self._chat_state.dispatch_in_progress
            and self._chat_state.active_task_id is None
            and update.task_id is not None
        ):
            self.buffer(update)
            return ()

        session_id, workspace_key = self._session_scope()
        if (
            session_id
            and workspace_key
            and (
                update.session_id != session_id
                or update.workspace_key != workspace_key
            )
        ):
            logger.debug(
                "Ignoring update for inactive Session: %s/%s",
                update.workspace_key,
                update.session_id,
            )
            return ()

        if update.type in {"user.message", "user.correction"}:
            self._on_user_input()

        raw_step = update.payload.get("step")
        assistant_key = (update.task_id, raw_step)
        if (
            update.type == "assistant.text_delta"
            and update.task_id is not None
            and isinstance(raw_step, int)
            and assistant_key in self._completed_assistant_steps
        ):
            return ()

        if (
            update.sequence is not None
            and update.sequence <= self.last_sequence
        ):
            return ()

        if (
            update.sequence is not None
            and (
                update.sequence <= self.last_sequence
                or (
                    not historical
                    and update.sequence != self.last_sequence + 1
                )
            )
        ):
            raise UpdateFatal(
                "RuntimeUpdate sequence",
                RuntimeError(
                    "Session history has a sequence gap: "
                    f"expected={self.last_sequence + 1} "
                    f"actual={update.sequence}"
                ),
            )

        try:
            actions = self._projectors.project(
                update,
                active_task_id=self._chat_state.active_task_id,
                include_unrelated=(
                    historical or update.sequence is not None
                ),
                historical=historical,
            )
        except Exception as error:
            raise UpdateFatal("event projection", error) from error

        filtered: list[UiAction] = []
        for action in actions:
            if (
                isinstance(action, SetRuntimeStatus)
                and (
                    historical
                    or action.task_id != self._chat_state.active_task_id
                )
            ):
                continue
            filtered.append(action)

        if filtered:
            try:
                await self._on_actions(tuple(filtered))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise UpdateFatal("UI action routing", error) from error

        if (
            update.type == "assistant.message"
            and update.task_id is not None
            and isinstance(raw_step, int)
        ):
            self._completed_assistant_steps.add(assistant_key)
        if update.sequence is not None:
            self.last_sequence = update.sequence
        return tuple(filtered)

    async def project_status(
        self,
        client: RuntimeClient,
        task_id: str,
        status: dict[str, Any],
    ) -> tuple[UiAction, ...]:
        lifecycle = status.get("lifecycle")
        if lifecycle not in {"completed", "failed", "cancelled"}:
            return ()
        update = RuntimeUpdateModel(
            workspace_key=str(getattr(client, "workspace_key", "unknown")),
            session_id=str(getattr(client, "session_id", "unknown")),
            task_id=task_id,
            type="task.finished",
            payload={
                "status": lifecycle,
                "run_id": status.get("run_id"),
            },
            occurred_at=datetime.now(UTC),
        )
        return await self.project(update)

    async def reconcile(
        self,
        client: RuntimeClient,
        task_id: str,
    ) -> tuple[UiAction, ...]:
        try:
            status = await client.get_task_status(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Unable to reconcile task status", exc_info=True)
            return ()
        return await self.project_status(client, task_id, status)
