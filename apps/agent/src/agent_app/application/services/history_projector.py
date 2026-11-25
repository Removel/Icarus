"""History projector application behavior."""

from __future__ import annotations


from pathlib import Path
from collections.abc import Sequence
from typing import Literal

from icarus.core.identity import SessionIdentity
from agent_app.contracts.persona import PersonaEvidence, PersonaSnapshot
from agent_app.application.repositories.session_store import SessionNotFoundError
from icarus.core.runtime_update import RuntimeUpdate


from agent_app.application.services.common import _coalesce_legacy_assistant_deltas


def persona_snapshot(identity: SessionIdentity, persona_id: str,
                     records: Sequence[RuntimeUpdate], start_sequence: int,
                     end_sequence: int) -> PersonaSnapshot:
    """Project only persisted user evidence and successful final assistant text."""
    selected = [item for item in records if item.sequence is not None and item.sequence <= end_sequence]
    statuses = {item.task_id: str(item.payload["status"]) for item in selected
                if item.type == "task.finished"}
    if any(item.task_id and item.task_id not in statuses for item in selected):
        raise ValueError("history_busy")
    final_assistants = {item.task_id: item.sequence for item in selected
                        if item.type == "assistant.message"}
    messages = []
    for item in selected:
        assert item.sequence is not None
        if item.sequence <= start_sequence:
            continue
        role: Literal["user", "assistant"]
        if item.type in {"user.message", "user.correction"}:
            role = "user"
        elif (item.type == "assistant.message" and statuses.get(item.task_id) == "completed"
              and final_assistants.get(item.task_id) == item.sequence
              and item.payload.get("has_tool_calls") is not True):
            role = "assistant"
        else:
            continue
        text = item.payload.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(PersonaEvidence(item.sequence, role, text, item.task_id, statuses.get(item.task_id)))
    return PersonaSnapshot(identity, persona_id, start_sequence, end_sequence, tuple(messages))


class HistoryProjector:
    async def page(self, workspace_path, session_id, *, after_sequence=0, limit=200):
        from agent_app.application.models.history import HistoryPage
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("history limit must be between 1 and 500")
        records, cursor = await self.get_session_history(workspace_path, session_id, after_sequence=after_sequence)
        page = records[:limit]
        return HistoryPage(page, cursor, (page[-1].sequence or after_sequence) if page else after_sequence, len(records) > len(page))

    def __init__(self, *, clock, entry, require_accepting, require_not_discarding, store, updates):
        self._clock = clock
        self._entry = entry
        self._require_accepting = require_accepting
        self._require_not_discarding = require_not_discarding
        self._store = store
        self._updates = updates

    async def get_session_history(
        self,
        workspace_path: str | Path,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[tuple[RuntimeUpdate, ...], int]:
        self._require_accepting()
        identity = SessionIdentity.create(workspace_path, session_id)
        if not await self._store().session_exists(identity):
            raise SessionNotFoundError(session_id)
        entry = await self._entry(identity)
        async with entry.mutation_lock:
            self._require_not_discarding(entry)
            store = self._store()
            all_records, cursor = await store.read_updates(identity)
            terminal_task_ids = {
                item.task_id
                for item in all_records
                if item.type == "task.finished" and item.task_id is not None
            }
            seen_task_ids = {
                item.task_id
                for item in all_records
                if item.task_id is not None
            }
            runtime_snapshot = (
                entry.runtime.snapshot()
                if entry.runtime is not None
                else None
            )
            active_task_ids = (
                set(runtime_snapshot.active_task_ids)
                if runtime_snapshot is not None
                else set()
            )
            active_task_ids.update(
                task_id
                for task_id, status in entry.tasks.items()
                if status.lifecycle in {"accepted", "running"}
            )
            interrupted_task_ids = (
                seen_task_ids - terminal_task_ids - active_task_ids
            )
            for task_id in sorted(interrupted_task_ids):
                interrupted = await store.append_update(
                    identity,
                    RuntimeUpdate(
                        workspace_key=identity.workspace_key,
                        session_id=identity.session_id,
                        task_id=task_id,
                        type="task.finished",
                        payload={
                            "status": "interrupted",
                            "run_id": None,
                            "recovered": True,
                        },
                        occurred_at=self._clock(),
                    ),
                )
                all_records = (*all_records, interrupted)
                cursor = interrupted.sequence or cursor
                await self._updates.publish(interrupted)
            compacted_records = _coalesce_legacy_assistant_deltas(all_records)
            return (
                tuple(
                    item
                    for item in compacted_records
                    if item.sequence is not None
                    and item.sequence > after_sequence
                ),
                cursor,
            )

